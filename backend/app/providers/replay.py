"""Deterministic Replay provider — the demo backbone, shaped as QUIET-WITH-SCHEDULED-EVENTS.

Why this shape: the product is TRIAGE ("2 of these deserve attention"). A generator where everything
wiggles several percent makes every symbol always flag and the digest reads as a price grid. So most of
the time symbols drift quietly (well under 1 sigma), and each symbol replays one scripted event per
20-minute period at a deterministic offset.

Why it's honest: prices are ANCHORED to each stock's REAL last close and events are SIZED in the stock's
REAL daily sigma (both from symbol_baselines via set_profile), so the app's own z-scores are legible and
not circular. Everything is a pure function of (symbol, seed, wall-clock), so the same seed replays the
same story every run and across processes. Data is labeled `is_simulated` in the UI — always.

Replay is NEVER used to train or validate anything (see ml/train_scorer.py: real candles only).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .base import Quote

PERIOD_S = 20 * 60           # one scripted scenario per symbol per 20-minute period
QUIET_AMP = 0.0012           # quiet drift ~0.12% -> well under 1 sigma over demo windows
EVENT_WINDOW_S = 5 * 60      # volume bursts last this long after an event
RETRACE_AFTER_S = 4 * 60     # for the spike-and-retrace scenario
# The app floors the since-seen window at 15 minutes: sigma_eff = sigma_daily * sqrt(15m / 6.25h) = 0.2*sigma.
# Events are sized in that unit so a k-step reads as ~k sigma right after it happens, and still >= 2 sigma
# if the user was away up to ~an hour (sqrt-time scaling halves it).
SIGMA_EFF_FACTOR = 0.2

DEFAULT_SIGMA = 0.015        # 1.5%/day if no real baseline yet
DEFAULT_AVG_VOL = 3_000_000


@dataclass
class Profile:
    anchor: float            # real last close (or fallback)
    sigma_daily: float       # real daily return stdev
    avg_vol: float           # real 20d average volume


# Scripted roles for the default demo symbols so the digest is legible: a mix of quiet and events.
# Unknown symbols get a deterministic role from their hash. The index is always quiet.
_ROLES = {
    "RELIANCE.NS": "sigma_down_volume",   # -2.3 sigma step on 3.2x volume
    "TCS.NS": "quiet",                    # nothing unusual — "all caught up" material
    "INFY.NS": "spike_retrace",           # +3 sigma spike that retraces (path matters)
    "HDFCBANK.NS": "sigma_up",
    "^NSEI": "quiet",
}
_ROLE_CYCLE = ["quiet", "sigma_up", "sigma_down_volume", "spike_retrace"]

# Fallback anchors if no real baseline has been loaded yet.
_FALLBACK_ANCHORS = {"RELIANCE.NS": 1322.0, "TCS.NS": 2304.0, "INFY.NS": 1130.0, "^NSEI": 23898.0}


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


class ReplayProvider:
    name = "replay"

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self._profiles: dict[str, Profile] = {}

    # ---- profile from real baselines (called by the app after ensure_baselines)
    def set_profile(self, symbol: str, anchor: float, sigma_daily: float, avg_vol: float) -> None:
        self._profiles[symbol] = Profile(anchor=anchor, sigma_daily=sigma_daily, avg_vol=avg_vol)

    def _profile(self, symbol: str) -> Profile:
        p = self._profiles.get(symbol)
        if p:
            return p
        anchor = _FALLBACK_ANCHORS.get(symbol, 100.0 + (_h(symbol) % 4000))
        return Profile(anchor=anchor, sigma_daily=DEFAULT_SIGMA, avg_vol=DEFAULT_AVG_VOL)

    # ---- deterministic scenario plumbing
    def _role(self, symbol: str) -> str:
        return _ROLES.get(symbol) or _ROLE_CYCLE[_h(f"{self.seed}:{symbol}") % len(_ROLE_CYCLE)]

    def _phase(self, symbol: str) -> float:
        return (_h(f"{self.seed}:{symbol}:phase") / 0xFFFFFFFF) * 2 * math.pi

    def _event_offset(self, symbol: str) -> float:
        """When in each 20-min period this symbol's event fires: deterministic, in [3, 12] minutes."""
        return 180.0 + (_h(f"{self.seed}:{symbol}:event") % 540)

    def _quiet_drift(self, symbol: str, t: float) -> float:
        ph = self._phase(symbol)
        return QUIET_AMP * (math.sin(2 * math.pi * t / 613.0 + ph) + 0.5 * math.sin(2 * math.pi * t / 151.0 + 2 * ph))

    def _event(self, symbol: str, t: float, sigma_eff: float) -> tuple[float, float]:
        """(price offset fraction, volume multiplier) from this period's scripted event, if it has fired."""
        role = self._role(symbol)
        if role == "quiet":
            return 0.0, 1.0
        since = (t % PERIOD_S) - self._event_offset(symbol)   # seconds since the event; <0 => not yet
        if since < 0:
            return 0.0, 1.0
        in_burst = since <= EVENT_WINDOW_S
        if role == "sigma_up":
            return 4.5 * sigma_eff, (2.0 if in_burst else 1.0)
        if role == "sigma_down_volume":
            return -4.0 * sigma_eff, (3.2 if in_burst else 1.2)
        if role == "spike_retrace":
            # +5 sigma_eff spike, then retraces to +1.5 sigma_eff after RETRACE_AFTER_S. Endpoint-diff misses
            # most of this; the path (peak excursion) does not.
            return (5.0 * sigma_eff if since < RETRACE_AFTER_S else 1.5 * sigma_eff), (2.5 if in_burst else 1.0)
        return 0.0, 1.0

    def _price_and_volume(self, symbol: str, t: float) -> tuple[float, int]:
        p = self._profile(symbol)
        sigma_eff = p.sigma_daily * SIGMA_EFF_FACTOR
        ev_price, ev_vol = self._event(symbol, t, sigma_eff)
        price = p.anchor * (1.0 + self._quiet_drift(symbol, t) + ev_price)
        noise = 0.9 + 0.2 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 97.0 + self._phase(symbol)))
        return round(price, 2), int(p.avg_vol * noise * ev_vol)

    def price_volume_at(self, symbol: str, t: float) -> tuple[float, int]:
        """Public: the deterministic (price, volume) at unix time t — enables exact demo rewinds."""
        return self._price_and_volume(symbol, t)

    # ---- MarketDataProvider
    async def get_quote(self, symbol: str) -> Quote:
        now = datetime.now(timezone.utc)
        price, volume = self._price_and_volume(symbol, now.timestamp())
        return Quote(symbol=symbol, price=price, volume=volume, event_time=now, source=self.name)

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        return {s: await self.get_quote(s) for s in symbols}
