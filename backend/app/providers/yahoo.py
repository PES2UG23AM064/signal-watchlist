"""Yahoo Finance provider — the REAL data source.

Uses one unauthenticated chart endpoint (no API key) that returns, in a single call, the live quote
(price, volume, exchange event-time) AND a full daily-candle history. Two roles:
  * get_history(): backfills real 1y daily candles -> honest volatility/volume/52w/beta baselines and
    the raw material for the self-validating backtest. Always used, regardless of the live provider.
  * get_quote(): an opportunistic live quote path (Replay stays the deterministic demo backbone).

Failures raise YahooError; callers (poller, baselines) catch and degrade — a flaky upstream must never
take the app down.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Sequence
from urllib.parse import quote as urlquote

import httpx

from .base import Quote

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0"}  # Yahoo rejects the default httpx UA
_TIMEOUT = 15.0


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

    async def _chart(self, symbol: str, range_: str, interval: str) -> dict:
        url = _CHART.format(symbol=urlquote(symbol, safe=""))  # '^NSEI' -> '%5ENSEI'
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
                resp = await client.get(url, params={"range": range_, "interval": interval})
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
        r = await self._chart(symbol, "1d", "1m")
        m = r.get("meta", {})
        price = m.get("regularMarketPrice")
        ts = m.get("regularMarketTime")
        if price is None or ts is None:
            raise YahooError(f"no live quote fields for {symbol}")
        return Quote(
            symbol=symbol,
            price=float(price),
            volume=int(m.get("regularMarketVolume") or 0),
            event_time=datetime.fromtimestamp(int(ts), tz=timezone.utc),  # exchange event-time
            source=self.name,
        )

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for s in symbols:  # sequential on purpose: gentle on an unauthenticated endpoint
            out[s] = await self.get_quote(s)
        return out

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
                    day=datetime.fromtimestamp(int(t), tz=timezone.utc).date(),
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
