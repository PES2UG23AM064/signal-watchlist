"""Composite provider: a REAL live feed (Yahoo) with a circuit breaker and an honest fallback (Replay).

Routing per quote:
  * market closed  -> fallback. Yahoo's quote is static outside NSE hours; the Replay simulator keeps the
                      app alive and demonstrable, and the source badge says "simulated" — never a lie.
  * breaker OPEN   -> fallback, without touching upstream (a failing dependency is not hammered).
  * otherwise      -> try the primary; on failure record it and fall back for THIS quote.

Circuit breaker: N consecutive failures open it for `cooldown_s`; after cooldown one trial call is allowed
(half-open); success closes it, failure re-opens it. Classic, small, and every state is observable via
`state()` for the observability panel. Each Quote carries the source that actually produced it, so
provenance/is_simulated stay truthful whichever path served it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Sequence

from ..market import market_status
from .base import MarketDataProvider, Quote

log = logging.getLogger("provider.composite")


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_s: float = 60.0
    consecutive_failures: int = 0
    opened_at: float | None = None
    total_failures: int = 0
    total_fallbacks: int = 0
    _clock: object = field(default=time.time, repr=False)

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if self._clock() - self.opened_at >= self.cooldown_s:
            return False  # half-open: allow a trial call
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.total_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_at = self._clock()

    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half-open" if not self.is_open() else "open"


class CompositeProvider:
    name = "composite"

    def __init__(self, primary: MarketDataProvider, fallback: MarketDataProvider,
                 breaker: CircuitBreaker | None = None, market_open=None,
                 secondary: MarketDataProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.secondary = secondary        # optional second REAL feed: a cross-check, never served
        self.breaker = breaker or CircuitBreaker()
        self._market_open = market_open or (lambda: market_status().is_open)
        self.last_route: str = "fallback"

    async def get_secondary_quote(self, symbol: str) -> Quote | None:
        """Best-effort cross-check quote from the secondary feed (only meaningful while the market is
        open and a secondary is configured). Failures are swallowed — a cross-check must never hurt."""
        if self.secondary is None or not self._market_open():
            return None
        try:
            return await self.secondary.get_quote(symbol)
        except Exception as e:  # noqa: BLE001
            log.info("secondary %s unavailable for %s: %s", self.secondary.name, symbol, e)
            return None

    def serving_live(self) -> bool:
        """True when a quote requested right now would come from the live primary."""
        return self._market_open() and not self.breaker.is_open()

    async def get_quote(self, symbol: str) -> Quote:
        if not self._market_open():
            self.last_route = "fallback:market-closed"
            return await self.fallback.get_quote(symbol)
        if self.breaker.is_open():
            self.last_route = "fallback:breaker-open"
            self.breaker.total_fallbacks += 1
            return await self.fallback.get_quote(symbol)
        try:
            q = await self.primary.get_quote(symbol)
        except Exception as e:  # noqa: BLE001 — any upstream failure trips the breaker, never the app
            self.breaker.record_failure()
            self.breaker.total_fallbacks += 1
            self.last_route = f"fallback:primary-error ({type(e).__name__})"
            log.warning("primary %s failed for %s (%s); breaker=%s", self.primary.name, symbol, e, self.breaker.state())
            return await self.fallback.get_quote(symbol)
        self.breaker.record_success()
        self.last_route = "primary"
        return q

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        return {s: await self.get_quote(s) for s in symbols}

    def status(self) -> dict:
        return {"primary": self.primary.name, "fallback": self.fallback.name, "breaker": self.breaker.state(),
                "secondary": self.secondary.name if self.secondary else None,
                "consecutive_failures": self.breaker.consecutive_failures,
                "total_failures": self.breaker.total_failures, "total_fallbacks": self.breaker.total_fallbacks,
                "market_open": self._market_open(), "serving_live": self.serving_live(), "last_route": self.last_route}
