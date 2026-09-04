"""Deterministic Replay provider — the demo backbone.

Given a seed, prices are a pure function of (symbol, wall-clock time), so:
  * the app is alive whenever anyone looks (NSE closed or not),
  * "leave and come back" always shows real movement, and
  * the same seed replays the same story every run (reproducible demos).

The series is a sum of sine waves (smooth, mean-reverting-looking) plus a slow drift. No RNG state
is kept between calls; everything derives from time, so it is horizontally identical across processes.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Sequence

from .base import Quote

# Anchor prices for the default NSE demo symbols (roughly realistic). Unknown symbols get a
# deterministic base derived from the symbol name so any ticker "works".
_ANCHORS: dict[str, float] = {
    "RELIANCE.NS": 1328.0,
    "TCS.NS": 3200.0,
    "INFY.NS": 1500.0,
    "HDFCBANK.NS": 1650.0,
    "^NSEI": 24000.0,  # NIFTY 50 index, used later for index-relative scoring
}

# (period_seconds, amplitude_fraction) components. Short periods give visible intraday movement
# in a demo; the amplitudes sum to a few percent.
_WAVES = [(37.0, 0.006), (91.0, 0.010), (213.0, 0.014), (887.0, 0.020)]


def _seed_phase(symbol: str, seed: int) -> float:
    """Stable per-(symbol, seed) phase offset in radians."""
    h = hashlib.sha256(f"{seed}:{symbol}".encode()).hexdigest()
    return (int(h[:8], 16) / 0xFFFFFFFF) * 2 * math.pi


def _anchor(symbol: str) -> float:
    if symbol in _ANCHORS:
        return _ANCHORS[symbol]
    h = int(hashlib.sha256(symbol.encode()).hexdigest()[:6], 16)
    return 100.0 + (h % 4000)  # deterministic base in [100, 4100)


class ReplayProvider:
    name = "replay"

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed

    def _price_at(self, symbol: str, t: float) -> float:
        base = _anchor(symbol)
        phase = _seed_phase(symbol, self.seed)
        frac = sum(amp * math.sin(2 * math.pi * t / period + phase) for period, amp in _WAVES)
        return round(base * (1 + frac), 2)

    def _volume_at(self, symbol: str, t: float) -> int:
        # A gently varying "normal" volume with occasional deterministic bursts.
        base = 2_000_000 + (int(hashlib.sha256(symbol.encode()).hexdigest()[:4], 16) % 3_000_000)
        burst = 1.0 + 0.5 * (math.sin(2 * math.pi * t / 149.0 + _seed_phase(symbol, self.seed)) ** 8)
        return int(base * burst)

    async def get_quote(self, symbol: str) -> Quote:
        now = datetime.now(timezone.utc)
        t = now.timestamp()
        return Quote(
            symbol=symbol,
            price=self._price_at(symbol, t),
            volume=self._volume_at(symbol, t),
            event_time=now,
            source=self.name,
        )

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        return {s: await self.get_quote(s) for s in symbols}
