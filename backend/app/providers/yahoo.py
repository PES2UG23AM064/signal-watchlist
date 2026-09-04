"""Yahoo Finance provider — the REAL data source.

Uses one unauthenticated chart endpoint (no API key) that returns, in a single call, the live quote
(price, volume, exchange event-time) AND a full daily-candle history. Two roles:
  * get_history(): backfills real 1y daily candles -> honest volatility/volume/52w/beta baselines and
    the raw material for the self-validating backtest. Always used, regardless of the live provider.
  * get_quote(): an opportunistic live quote path (Replay stays the deterministic demo backbone).

Good-citizen plumbing for an unauthenticated endpoint: one keep-alive client, a token bucket
(`rate_per_min`, which the poller reads to stretch its interval rather than wedge), and bounded
concurrency. Failures raise YahooError; callers (composite/breaker, baselines) degrade — a flaky upstream
must never take the app down.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import quote as urlquote

import httpx

from .base import Quote

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0"}  # Yahoo rejects the default httpx UA
_TIMEOUT = 15.0
RATE_PER_MIN = 30        # be a good citizen on an unauthenticated endpoint
MAX_CONCURRENCY = 4      # bounded fan-out: N symbols do not become N serialized round-trips, nor a burst


class _TokenBucket:
    """Async token bucket: `rate` tokens per minute, burst up to `capacity`. Waiters sleep OUTSIDE the
    lock, so one sleeper never serializes everyone else behind it."""

    def __init__(self, rate_per_min: float, capacity: int) -> None:
        self.rate = rate_per_min / 60.0
        self.capacity = capacity
        self.tokens = float(capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            await asyncio.sleep(wait)


class YahooError(Exception):
    """Upstream failed, rate-limited, or returned an unusable payload."""


@dataclass(frozen=True)
class Candle:
    day: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int


class YahooProvider:
    name = "yahoo"
    rate_per_min = RATE_PER_MIN  # read by the poller to size its interval

    def __init__(self) -> None:
        self._bucket = _TokenBucket(RATE_PER_MIN, capacity=RATE_PER_MIN)
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)
        self._client: httpx.AsyncClient | None = None  # one keep-alive client; created lazily in-loop

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS)
        return self._client

    async def _chart(self, symbol: str, range_: str, interval: str) -> dict:
        url = _CHART.format(symbol=urlquote(symbol, safe=""))  # '^NSEI' -> '%5ENSEI'
        await self._bucket.take()
        try:
            resp = await self._http().get(url, params={"range": range_, "interval": interval})
        except httpx.HTTPError as e:
            raise YahooError(f"network error for {symbol}: {e}") from e
        if resp.status_code == 429:
            raise YahooError(f"rate limited fetching {symbol}")
        if resp.status_code != 200:
            raise YahooError(f"HTTP {resp.status_code} for {symbol}")
        try:
            result = resp.json()["chart"]["result"][0]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise YahooError(f"unusable payload for {symbol}") from e
        return result

    async def get_quote(self, symbol: str) -> Quote:
        # The live fields live in `meta`; ask for the smallest payload that carries them (one daily bar),
        # not 375 one-minute bars we'd throw away.
        r = await self._chart(symbol, "1d", "1d")
        m = r.get("meta", {})
        price = m.get("regularMarketPrice")
        ts = m.get("regularMarketTime")
        if price is None or ts is None:
            raise YahooError(f"no live quote fields for {symbol}")
        return Quote(
            symbol=symbol,
            price=float(price),
            volume=int(m.get("regularMarketVolume") or 0),
            event_time=datetime.fromtimestamp(int(ts), tz=UTC),  # exchange event-time
            source=self.name,
        )

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        """Bounded concurrency + the token bucket: fast enough that a poll cycle doesn't fall behind the
        interval as the watchlist grows, gentle enough not to get rate-limited. A failing symbol raises
        from here so the caller (composite/breaker) can fall back for the batch."""
        async def one(s: str) -> tuple[str, Quote]:
            async with self._sem:
                return s, await self.get_quote(s)
        results = await asyncio.gather(*(one(s) for s in symbols))
        return dict(results)

    async def get_history(self, symbol: str, range_: str = "1y") -> list[Candle]:
        """Daily candles, oldest -> newest. Bars with a missing close are dropped."""
        r = await self._chart(symbol, range_, "1d")
        ts = r.get("timestamp") or []
        q = (r.get("indicators", {}).get("quote") or [{}])[0]
        closes, vols = q.get("close") or [], q.get("volume") or []
        opens, highs, lows = q.get("open") or [], q.get("high") or [], q.get("low") or []
        out: list[Candle] = []
        for i, t in enumerate(ts):
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            out.append(
                Candle(
                    day=datetime.fromtimestamp(int(t), tz=UTC).date(),
                    open=opens[i] if i < len(opens) else None,
                    high=highs[i] if i < len(highs) else None,
                    low=lows[i] if i < len(lows) else None,
                    close=float(c),
                    volume=int(vols[i] or 0) if i < len(vols) else 0,
                )
            )
        if len(out) < 30:
            raise YahooError(f"too little history for {symbol} ({len(out)} bars)")
        return out
