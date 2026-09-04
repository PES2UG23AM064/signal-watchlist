"""Composite provider + circuit breaker: the live feed never takes the app down, the fallback is honest
about its source, and a failing upstream is not hammered while the breaker is open."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.providers.base import Quote
from app.providers.composite import CircuitBreaker, CompositeProvider


class FakeLive:
    name = "yahoo"
    def __init__(self): self.calls = 0; self.fail = False
    async def get_quote(self, symbol):
        self.calls += 1
        if self.fail: raise RuntimeError("upstream down")
        return Quote(symbol=symbol, price=100.0, volume=1, event_time=datetime.now(timezone.utc), source=self.name)
    async def get_quotes(self, symbols): return {s: await self.get_quote(s) for s in symbols}


class FakeReplay:
    name = "replay"
    async def get_quote(self, symbol):
        return Quote(symbol=symbol, price=99.0, volume=1, event_time=datetime.now(timezone.utc), source=self.name)
    async def get_quotes(self, symbols): return {s: await self.get_quote(s) for s in symbols}


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def _cp(market_open=True, clock=None):
    live, replay = FakeLive(), FakeReplay()
    br = CircuitBreaker(failure_threshold=3, cooldown_s=60, _clock=clock or Clock())
    return CompositeProvider(live, replay, br, market_open=lambda: market_open), live, replay


def test_market_closed_routes_to_fallback_and_labels_source():
    cp, live, _ = _cp(market_open=False)
    q = asyncio.run(cp.get_quote("X.NS"))
    assert q.source == "replay" and live.calls == 0 and cp.last_route.startswith("fallback:market-closed")


def test_primary_used_when_open_and_healthy():
    cp, live, _ = _cp()
    q = asyncio.run(cp.get_quote("X.NS"))
    assert q.source == "yahoo" and live.calls == 1 and cp.breaker.state() == "closed"


def test_failure_falls_back_for_that_quote_and_trips_breaker_after_threshold():
    cp, live, _ = _cp()
    live.fail = True
    for i in range(3):
        q = asyncio.run(cp.get_quote("X.NS"))
        assert q.source == "replay"          # never an exception, never a missing quote
    assert cp.breaker.state() == "open" and live.calls == 3


def test_open_breaker_does_not_hammer_upstream_then_half_opens_after_cooldown():
    clock = Clock()
    cp, live, _ = _cp(clock=clock)
    live.fail = True
    for _ in range(3):
        asyncio.run(cp.get_quote("X.NS"))
    calls_when_opened = live.calls
    asyncio.run(cp.get_quote("X.NS"))               # open: no upstream call
    assert live.calls == calls_when_opened and cp.last_route == "fallback:breaker-open"
    clock.t += 61                                    # cooldown elapsed -> half-open trial
    live.fail = False
    q = asyncio.run(cp.get_quote("X.NS"))
    assert q.source == "yahoo" and live.calls == calls_when_opened + 1 and cp.breaker.state() == "closed"


class FakeSecondary:
    name = "twelvedata"
    def __init__(self, price=100.5, fail=False): self.price = price; self.fail = fail
    async def get_quote(self, symbol):
        if self.fail: raise RuntimeError("secondary down")
        return Quote(symbol=symbol, price=self.price, volume=1, event_time=datetime.now(timezone.utc), source=self.name)
    async def get_quotes(self, symbols): return {s: await self.get_quote(s) for s in symbols}


def test_secondary_is_a_best_effort_cross_check_never_an_error():
    live, replay = FakeLive(), FakeReplay()
    cp = CompositeProvider(live, replay, CircuitBreaker(_clock=Clock()), market_open=lambda: True, secondary=FakeSecondary())
    q2 = asyncio.run(cp.get_secondary_quote("X.NS"))
    assert q2 is not None and q2.source == "twelvedata" and q2.price == 100.5
    # served path is untouched by the secondary
    assert asyncio.run(cp.get_quote("X.NS")).source == "yahoo"
    # a failing secondary returns None, never raises
    cp.secondary = FakeSecondary(fail=True)
    assert asyncio.run(cp.get_secondary_quote("X.NS")) is None
    # no secondary configured / market closed -> None
    assert asyncio.run(CompositeProvider(live, replay, market_open=lambda: True).get_secondary_quote("X.NS")) is None
    cp2 = CompositeProvider(live, replay, market_open=lambda: False, secondary=FakeSecondary())
    assert asyncio.run(cp2.get_secondary_quote("X.NS")) is None


def test_status_is_observable():
    cp, live, _ = _cp()
    live.fail = True
    asyncio.run(cp.get_quote("X.NS"))
    s = cp.status()
    assert s["primary"] == "yahoo" and s["fallback"] == "replay" and s["total_fallbacks"] == 1
    assert s["breaker"] == "closed" and s["consecutive_failures"] == 1 and s["market_open"] is True
