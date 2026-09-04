"""Deterministic Replay provider: quiet drift with one scripted event per symbol per 20-minute period.

Prices are anchored to each stock's real last close and events are sized in its real daily sigma
(set_profile), so the app's z-scores are not circular. Everything is a pure function of
(symbol, seed, wall-clock). Symbols without a real anchor are never given an invented price.
Packs (set_groups) share a sector-day move every other period; one designated member also keeps its
own event so "moving alone" occurs.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from .base import Quote

PERIOD_S = 20 * 60           # one scripted scenario per symbol per 20-minute period
QUIET_AMP = 0.0012           # quiet drift ~0.12% -> well under 1 sigma over demo windows
EVENT_WINDOW_S = 5 * 60      # volume bursts last this long after an event
RETRACE_AFTER_S = 4 * 60     # for the spike-and-retrace scenario
# Events are permanent steps that alternate direction, so the level is bounded and flat between events:
# a snap-back to the anchor at a period boundary would register as a second, opposite, unusual move.
# The app floors the since-seen window at 15 min: sigma_eff = sigma_daily * sqrt(15m / 6.25h) = 0.2 * sigma.
# Events are sized in that unit so a k-step reads as ~k sigma right after it fires.
SIGMA_EFF_FACTOR = 0.2
# Pack move shared by every member, sized in sigma_eff of the pack's median sigma so all members flag.
PACK_SIGMA = 3.0
PACK_VOL = 1.8

DEFAULT_SIGMA = 0.015        # 1.5%/day if no real baseline yet (default demo names only)
DEFAULT_AVG_VOL = 3_000_000


class NoAnchor(Exception):
    """The simulator has no real price level for this symbol and refuses to invent one."""


@dataclass
class Profile:
    anchor: float            # real last close (or a built-in anchor for the default demo names)
    sigma_daily: float       # real daily return stdev
    avg_vol: float           # real 20d average volume


# Scripted roles for the default demo symbols; other symbols get a deterministic role from their hash.
_ROLES = {
    "RELIANCE.NS": "sigma_down_volume",
    "TCS.NS": "quiet",
    "INFY.NS": "spike_retrace",
    "HDFCBANK.NS": "sigma_up",
    "^NSEI": "quiet",
}
_ROLE_CYCLE = ["quiet", "sigma_up", "sigma_down_volume", "spike_retrace"]

# Anchors so the default demo works before any baseline has loaded (real closes as of 2026-09-04).
_FALLBACK_ANCHORS = {"RELIANCE.NS": 1322.0, "TCS.NS": 2304.0, "INFY.NS": 1130.0, "^NSEI": 23898.0}


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


class ReplayProvider:
    name = "replay"

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self._profiles: dict[str, Profile] = {}
        self._group_of: dict[str, tuple[str, ...]] = {}   # symbol -> its pack (sorted members)

    # ---- profile from real baselines
    def set_profile(self, symbol: str, anchor: float, sigma_daily: float, avg_vol: float) -> None:
        self._profiles[symbol] = Profile(anchor=anchor, sigma_daily=sigma_daily, avg_vol=avg_vol)

    def has_profile(self, symbol: str) -> bool:
        """True if a real baseline profile has been loaded (built-in demo anchors do not count)."""
        return symbol in self._profiles

    def set_groups(self, groups: Sequence[Sequence[str]]) -> None:
        """Replace the packs with the given multi-member cohorts."""
        self._group_of = {}
        for g in groups:
            if len(g) < 2:
                continue
            members = tuple(sorted(g))
            for s in members:
                self._group_of[s] = members

    def _profile(self, symbol: str) -> Profile | None:
        p = self._profiles.get(symbol)
        if p:
            return p
        if symbol in _FALLBACK_ANCHORS:
            return Profile(anchor=_FALLBACK_ANCHORS[symbol], sigma_daily=DEFAULT_SIGMA, avg_vol=DEFAULT_AVG_VOL)
        return None

    def can_quote(self, symbol: str) -> bool:
        return self._profile(symbol) is not None

    # ---- deterministic scenario plumbing
    def _role(self, symbol: str) -> str:
        return _ROLES.get(symbol) or _ROLE_CYCLE[_h(f"{self.seed}:{symbol}") % len(_ROLE_CYCLE)]

    def _phase(self, symbol: str) -> float:
        return (_h(f"{self.seed}:{symbol}:phase") / 0xFFFFFFFF) * 2 * math.pi

    def _event_offset(self, key: str) -> float:
        """Seconds into each period at which this symbol's (or pack's) event fires: in [3, 12] minutes."""
        return 180.0 + (_h(f"{self.seed}:{key}:event") % 540)

    def _quiet_drift(self, symbol: str, t: float) -> float:
        ph = self._phase(symbol)
        return QUIET_AMP * (math.sin(2 * math.pi * t / 613.0 + ph) + 0.5 * math.sin(2 * math.pi * t / 151.0 + 2 * ph))

    @staticmethod
    def _shape(role: str, since: float) -> tuple[float, float]:
        """(step in sigma_eff units, volume multiplier) of one scripted event `since` seconds after it fired."""
        in_burst = since <= EVENT_WINDOW_S
        if role == "sigma_up":
            return 4.5, (2.0 if in_burst else 1.0)
        if role == "sigma_down_volume":
            return -4.0, (3.2 if in_burst else 1.2)
        if role == "spike_retrace":
            # +5 sigma_eff spike, retracing to +1.5 after RETRACE_AFTER_S; only the path sees most of it.
            return (5.0 if since < RETRACE_AFTER_S else 1.5), (2.5 if in_burst else 1.0)
        return 0.0, 1.0

    @staticmethod
    def _level(step_now: float | None, final: float, n_before: int) -> float:
        """Cumulative level of alternating permanent steps: n_before completed events leave `final` if
        n_before is odd else 0; the current event (if fired) adds its step with the next sign."""
        level = final if n_before % 2 else 0.0
        if step_now is not None:
            level += step_now if n_before % 2 == 0 else -step_now
        return level

    def _event(self, symbol: str, t: float, sigma_eff: float) -> tuple[float, float]:
        """(price offset fraction, volume multiplier) from this symbol's scripted events."""
        role = self._role(symbol)
        if role == "quiet":
            return 0.0, 1.0
        period = int(t // PERIOD_S)
        since = (t % PERIOD_S) - self._event_offset(symbol)   # < 0 => this period's event has not fired yet
        final = self._shape(role, PERIOD_S)[0]
        if since < 0:
            return self._level(None, final, period) * sigma_eff, 1.0
        step, vol = self._shape(role, since)
        return self._level(step, final, period) * sigma_eff, vol

    # ---- packs
    def designated(self, symbol: str) -> bool:
        """The one pack member that keeps its own event: a scripted non-quiet role if any, else a
        deterministic pick. True for symbols outside any pack."""
        members = self._group_of.get(symbol)
        if members is None:
            return True
        scripted = [s for s in members if _ROLES.get(s) not in (None, "quiet")]
        pick = scripted[0] if scripted else max(members, key=lambda s: _h(f"{self.seed}:{s}:lead"))
        return symbol == pick

    def _pack_parity(self, key: str) -> int:
        return _h(f"{self.seed}:{key}:parity") % 2

    def pack_fires(self, symbol: str, period: int) -> bool:
        """A sector day every other period, so pack members are not flagged all the time."""
        members = self._group_of.get(symbol)
        if members is None:
            return False
        return period % 2 == self._pack_parity("pack:" + "|".join(members))

    def pack_offset_at(self, symbol: str, t: float) -> tuple[float, float]:
        """(shared price offset fraction, volume multiplier) of this symbol's pack at time t;
        (0, 1) if it is in no pack."""
        members = self._group_of.get(symbol)
        if members is None:
            return 0.0, 1.0
        key = "pack:" + "|".join(members)
        sigmas = [p.sigma_daily for p in (self._profile(s) for s in members) if p is not None]
        sigma_med = statistics.median(sigmas) if sigmas else DEFAULT_SIGMA
        step = PACK_SIGMA * SIGMA_EFF_FACTOR * sigma_med
        period = int(t // PERIOD_S)
        r = self._pack_parity(key)
        n_before = max(0, (period + 1 - r) // 2)     # sector days in periods [0, period)
        if not self.pack_fires(symbol, period):
            return self._level(None, step, n_before), 1.0
        since = (t % PERIOD_S) - self._event_offset(key)
        if since < 0:
            return self._level(None, step, n_before), 1.0
        return self._level(step, step, n_before), (PACK_VOL if since <= EVENT_WINDOW_S else 1.1)

    def _price_and_volume(self, symbol: str, t: float) -> tuple[float, int]:
        p = self._profile(symbol)
        if p is None:
            raise NoAnchor(symbol)
        sigma_eff = p.sigma_daily * SIGMA_EFF_FACTOR
        pack_price, pack_vol = self.pack_offset_at(symbol, t)
        # Non-designated pack members move only with the pack.
        ev_price, ev_vol = self._event(symbol, t, sigma_eff) if self.designated(symbol) else (0.0, 1.0)
        price = p.anchor * (1.0 + self._quiet_drift(symbol, t) + pack_price + ev_price)
        noise = 0.9 + 0.2 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 97.0 + self._phase(symbol)))
        return round(price, 2), int(p.avg_vol * noise * max(ev_vol, pack_vol))

    def price_volume_at(self, symbol: str, t: float) -> tuple[float, int]:
        """The deterministic (price, volume) at unix time t; used for exact demo rewinds."""
        return self._price_and_volume(symbol, t)

    # ---- MarketDataProvider
    async def get_quote(self, symbol: str) -> Quote:
        now = datetime.now(UTC)
        price, volume = self._price_and_volume(symbol, now.timestamp())  # raises NoAnchor if unknown
        return Quote(symbol=symbol, price=price, volume=volume, event_time=now, source=self.name)

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        """Quotes for every symbol with a real anchor; unknown symbols are omitted rather than invented."""
        out: dict[str, Quote] = {}
        for s in symbols:
            if self.can_quote(s):
                out[s] = await self.get_quote(s)
        return out
