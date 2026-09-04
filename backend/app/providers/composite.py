"""Composite provider: a REAL live feed (Yahoo) with a circuit breaker and an honest fallback (Replay).

Routing per batch/quote:
  * market closed  -> fallback. Yahoo's quote is static outside NSE hours; the Replay simulator keeps the
                      app alive and demonstrable, and the source badge says "simulated" — never a lie.
  * breaker OPEN   -> fallback, without touching upstream (a failing dependency is not hammered).
  * half-open      -> exactly ONE trial request is admitted after the cooldown; everyone else falls back
                      until that trial succeeds (a recovering upstream must not get a thundering herd).
  * otherwise      -> try the primary; on failure record it and fall back for THIS batch.

Batches go to `primary.get_quotes` so the primary's own rate limiting and bounded concurrency apply
(calling get_quote in a loop would silently bypass them). Each Quote carries the source that actually
produced it, so provenance/is_simulated stay truthful whichever path served it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..market import market_status
from .base import MarketDataProvider, Quote

log = logging.getLogger("provider.composite")


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_s: float = 60.0
    consecutive_failures: int = 0
    opened_at: float | None = None
    trial_in_flight: bool = False
    total_failures: int = 0
    total_fallbacks: int = 0
    _clock: object = field(default=time.time, repr=False)

    def is_open(self) -> bool:
        """Pure: within the cooldown window after tripping."""
        return self.opened_at is not None and (self._clock() - self.opened_at) < self.cooldown_s

    def allow_request(self) -> bool:
        """Admission decision (mutating). closed -> yes; open -> no; half-open -> exactly one trial."""
        if self.opened_at is None:
            return True
        if self.is_open():
            return False
        if self.trial_in_flight:
            return False
        self.trial_in_flight = True
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None
        self.trial_in_flight = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.total_failures += 1
        self.trial_in_flight = False
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_at = self._clock()  # (re)open — also re-arms the cooldown after a failed trial

    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "open" if self.is_open() else "half-open"


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

    # ---- routing -------------------------------------------------------------------------------------
    def _route(self) -> str | None:
        """None => use the primary; otherwise the fallback reason."""
        if not self._market_open():
            return "fallback:market-closed"
        if not self.breaker.allow_request():
            return "fallback:breaker-open"
        return None

    async def get_quote(self, symbol: str) -> Quote:
        reason = self._route()
        if reason:
            self.last_route = reason
            self.breaker.total_fallbacks += 1
            return await self.fallback.get_quote(symbol)
        try:
            q = await self.primary.get_quote(symbol)
        except Exception as e:  # noqa: BLE001 — any upstream failure trips the breaker, never the app
            return await self._fail_over_one(symbol, e)
        self.breaker.record_success()
        self.last_route = "primary"
        return q

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        """Batch path — delegates to primary.get_quotes so its rate limiter + bounded concurrency apply."""
        reason = self._route()
        if reason:
            self.last_route = reason
            self.breaker.total_fallbacks += len(symbols)
            return await self.fallback.get_quotes(symbols)
        try:
            out = await self.primary.get_quotes(symbols)
        except Exception as e:  # noqa: BLE001
            self.breaker.record_failure()
            self.breaker.total_fallbacks += len(symbols)
            self.last_route = f"fallback:primary-error ({type(e).__name__})"
            log.warning("primary %s batch failed (%s); breaker=%s", self.primary.name, e, self.breaker.state())
            return await self.fallback.get_quotes(symbols)
        self.breaker.record_success()
        self.last_route = "primary"
        return out

    async def _fail_over_one(self, symbol: str, e: Exception) -> Quote:
        self.breaker.record_failure()
        self.breaker.total_fallbacks += 1
        self.last_route = f"fallback:primary-error ({type(e).__name__})"
        log.warning("primary %s failed for %s (%s); breaker=%s", self.primary.name, symbol, e, self.breaker.state())
        return await self.fallback.get_quote(symbol)

    def serving_live(self) -> bool:
        """True when a quote requested right now would come from the live primary."""
        return self._market_open() and not self.breaker.is_open()

    # ---- secondary (cross-check) ----------------------------------------------------------------------
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

    async def get_secondary_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        """Concurrent best-effort cross-checks; symbols the secondary can't quote are simply absent."""
        if self.secondary is None or not self._market_open():
            return {}
        results = await asyncio.gather(*(self.get_secondary_quote(s) for s in symbols))
        return {s: q for s, q in zip(symbols, results, strict=True) if q is not None}

    # ---- observability -------------------------------------------------------------------------------
    def status(self) -> dict:
        return {"primary": self.primary.name, "fallback": self.fallback.name, "breaker": self.breaker.state(),
                "secondary": self.secondary.name if self.secondary else None,
                "half_open_trial_in_flight": self.breaker.trial_in_flight,
                "consecutive_failures": self.breaker.consecutive_failures,
                "total_failures": self.breaker.total_failures, "total_fallbacks": self.breaker.total_fallbacks,
                "market_open": self._market_open(), "serving_live": self.serving_live(), "last_route": self.last_route}
