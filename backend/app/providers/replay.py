"""Deterministic Replay provider — the demo backbone, shaped as QUIET-WITH-SCHEDULED-EVENTS.

Why this shape: the product is TRIAGE ("2 of these deserve attention"). A generator where everything
wiggles several percent makes every symbol always flag and the digest reads as a price grid. So most of
the time symbols drift quietly (well under 1 sigma), and each symbol replays one scripted event per
20-minute period at a deterministic offset.

Why it's honest: prices are ANCHORED to each stock's REAL last close and events are SIZED in the stock's
REAL daily sigma (both from symbol_baselines via set_profile), so the app's own z-scores are legible and
not circular. Everything is a pure function of (symbol, seed, wall-clock), so the same seed replays the
same story every run and across processes. Data is labeled `is_simulated` in the UI — always.

Packs: the app tells the simulator which watched symbols the REAL candles say move together (the
co-movement cohorts, `set_groups`). Members of a pack share one scripted pack move every OTHER period — a
sector day — so the digest's group cards happen for the same reason they would in the market, but not so
often that every member is always flagged. Within a pack,
exactly one designated member keeps its own idiosyncratic event on top, so "moving alone" happens too.

Honesty rule: the simulator only quotes symbols it has a REAL anchor for (a loaded baseline, or the
built-in anchors for the default demo names). A symbol with no real data (e.g. a delisted ticker Yahoo
returns 404 for) is NOT given an invented price — it reads "no data" in the app instead.

Replay is NEVER used to train or validate anything (see ml/train_scorer.py: real candles only).
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
# An event is a PERMANENT step, and consecutive events on a symbol alternate direction. That is the only
# shape that is both bounded and calm: a price never "snaps back" to the anchor at a period boundary (which
# would register as a second, opposite, unusual move), and any bounded level that only ever stepped one way
# would have to bleed off a whole step between events — a 3-sigma "move" with no event behind it. So between
# events the level is flat (quiet drift only) and the ONLY things that flag are the events themselves.
# The app floors the since-seen window at 15 minutes: sigma_eff = sigma_daily * sqrt(15m / 6.25h) = 0.2*sigma.
# Events are sized in that unit so a k-step reads as ~k sigma right after it happens, and still >= 2 sigma
# if the user was away up to ~an hour (sqrt-time scaling halves it).
SIGMA_EFF_FACTOR = 0.2
# A pack move: one % move shared by every member (a sector day), sized at 3 sigma_eff of the pack's MEDIAN
# sigma so each member reads ~3 sigma in its own terms -> all flag -> they fold into one group card.
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


# Scripted roles for the default demo symbols so the digest is legible: a mix of quiet and events.
# Unknown symbols get a deterministic role from their hash. The index is always quiet.
_ROLES = {
    "RELIANCE.NS": "sigma_down_volume",   # 4 sigma_eff step (down first, then alternating) on 3.2x volume
    "TCS.NS": "quiet",                    # nothing unusual — "all caught up" material
    "INFY.NS": "spike_retrace",           # spike that retraces (path matters)
    "HDFCBANK.NS": "sigma_up",            # 4.5 sigma_eff step (up first, then alternating) on 2x volume
    "^NSEI": "quiet",
}
_ROLE_CYCLE = ["quiet", "sigma_up", "sigma_down_volume", "spike_retrace"]

# Built-in anchors so the default demo works before any baseline has loaded (real closes as of 2026-09-04).
_FALLBACK_ANCHORS = {"RELIANCE.NS": 1322.0, "TCS.NS": 2304.0, "INFY.NS": 1130.0, "^NSEI": 23898.0}


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


class ReplayProvider:
    name = "replay"

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self._profiles: dict[str, Profile] = {}
        self._group_of: dict[str, tuple[str, ...]] = {}   # symbol -> its pack (sorted members)

    # ---- profile from real baselines (called by the app after ensure_baselines)
    def set_profile(self, symbol: str, anchor: float, sigma_daily: float, avg_vol: float) -> None:
        self._profiles[symbol] = Profile(anchor=anchor, sigma_daily=sigma_daily, avg_vol=avg_vol)

    def has_profile(self, symbol: str) -> bool:
        """True if a REAL baseline profile has been loaded (built-in demo anchors don't count)."""
        return symbol in self._profiles

    def set_groups(self, groups: Sequence[Sequence[str]]) -> None:
        """Packs from the REAL co-movement cohorts (multi-member ones). Replaces the previous packs."""
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
        return None  # no real anchor -> refuse to invent a price

    def can_quote(self, symbol: str) -> bool:
        return self._profile(symbol) is not None

    # ---- deterministic scenario plumbing
    def _role(self, symbol: str) -> str:
        return _ROLES.get(symbol) or _ROLE_CYCLE[_h(f"{self.seed}:{symbol}") % len(_ROLE_CYCLE)]

    def _phase(self, symbol: str) -> float:
        return (_h(f"{self.seed}:{symbol}:phase") / 0xFFFFFFFF) * 2 * math.pi

    def _event_offset(self, key: str) -> float:
        """When in each 20-min period this symbol's (or pack's) event fires: deterministic, in [3, 12] minutes."""
        return 180.0 + (_h(f"{self.seed}:{key}:event") % 540)

    def _quiet_drift(self, symbol: str, t: float) -> float:
        ph = self._phase(symbol)
        return QUIET_AMP * (math.sin(2 * math.pi * t / 613.0 + ph) + 0.5 * math.sin(2 * math.pi * t / 151.0 + 2 * ph))

    @staticmethod
    def _shape(role: str, since: float) -> tuple[float, float]:
        """(step in sigma_eff units, volume multiplier) of one scripted event `since` seconds after it fired.
        The step is what the event ends at; before that the path may differ (the spike)."""
        in_burst = since <= EVENT_WINDOW_S
        if role == "sigma_up":
            return 4.5, (2.0 if in_burst else 1.0)
        if role == "sigma_down_volume":
            return -4.0, (3.2 if in_burst else 1.2)
        if role == "spike_retrace":
            # +5 sigma_eff spike, then retraces to +1.5 sigma_eff after RETRACE_AFTER_S. Endpoint-diff misses
            # most of this; the path (peak excursion) does not.
            return (5.0 if since < RETRACE_AFTER_S else 1.5), (2.5 if in_burst else 1.0)
        return 0.0, 1.0

    @staticmethod
    def _level(step_now: float | None, final: float, n_before: int) -> float:
        """Cumulative level of alternating permanent steps: n_before completed events (+final, -final, ...)
        leave `final` if n_before is odd else 0; the current event (if fired) adds its step with the next
        sign. Closed form, so the price stays a pure function of time — no history to replay."""
        level = final if n_before % 2 else 0.0
        if step_now is not None:
            level += step_now if n_before % 2 == 0 else -step_now
        return level

    def _event(self, symbol: str, t: float, sigma_eff: float) -> tuple[float, float]:
        """(price offset fraction, volume multiplier) from this symbol's scripted events: every period's
        event is a permanent step, alternating direction, so the level is bounded and flat between events."""
        role = self._role(symbol)
        if role == "quiet":
            return 0.0, 1.0
        period = int(t // PERIOD_S)
        since = (t % PERIOD_S) - self._event_offset(symbol)   # seconds since this period's event; <0 => not yet
        final = self._shape(role, PERIOD_S)[0]
        if since < 0:
            return self._level(None, final, period) * sigma_eff, 1.0
        step, vol = self._shape(role, since)
        return self._level(step, final, period) * sigma_eff, vol

    # ---- packs
    def designated(self, symbol: str) -> bool:
        """The ONE member of a pack that keeps its own idiosyncratic event (so it diverges from the pack).
        Prefer a member with an explicitly scripted non-quiet role; else a deterministic pick."""
        members = self._group_of.get(symbol)
        if members is None:
            return True
        scripted = [s for s in members if _ROLES.get(s) not in (None, "quiet")]
        pick = scripted[0] if scripted else max(members, key=lambda s: _h(f"{self.seed}:{s}:lead"))
        return symbol == pick

    def _pack_parity(self, key: str) -> int:
        return _h(f"{self.seed}:{key}:parity") % 2

    def pack_fires(self, symbol: str, period: int) -> bool:
        """A sector day every OTHER period, not every period: otherwise every pack member flags all the
        time and the digest reads "everything is unusual" — the opposite of triage. Deterministic per pack."""
        members = self._group_of.get(symbol)
        if members is None:
            return False
        return period % 2 == self._pack_parity("pack:" + "|".join(members))

    def pack_offset_at(self, symbol: str, t: float) -> tuple[float, float]:
        """(shared price offset fraction, volume multiplier) of this symbol's pack at time t: the
        cumulative level of its alternating sector days (up-day, down-day, ...); (0, 1) if no pack."""
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
        # Non-designated pack members move ONLY with the pack: that's what makes them a pack.
        ev_price, ev_vol = self._event(symbol, t, sigma_eff) if self.designated(symbol) else (0.0, 1.0)
        price = p.anchor * (1.0 + self._quiet_drift(symbol, t) + pack_price + ev_price)
        noise = 0.9 + 0.2 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 97.0 + self._phase(symbol)))
        return round(price, 2), int(p.avg_vol * noise * max(ev_vol, pack_vol))

    def price_volume_at(self, symbol: str, t: float) -> tuple[float, int]:
        """Public: the deterministic (price, volume) at unix time t — enables exact demo rewinds."""
        return self._price_and_volume(symbol, t)

    # ---- MarketDataProvider
    async def get_quote(self, symbol: str) -> Quote:
        now = datetime.now(UTC)
        price, volume = self._price_and_volume(symbol, now.timestamp())  # raises NoAnchor if unknown
        return Quote(symbol=symbol, price=price, volume=volume, event_time=now, source=self.name)

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        """Quotes for every symbol we have a real anchor for; unknown symbols are simply omitted (they
        read 'no data' downstream) rather than failing the whole batch or inventing a price."""
        out: dict[str, Quote] = {}
        for s in symbols:
            if self.can_quote(s):
                out[s] = await self.get_quote(s)
        return out
