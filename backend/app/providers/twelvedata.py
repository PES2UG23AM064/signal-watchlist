"""Twelve Data: an optional second real feed, used only as a cross-check (never served).

Indices are skipped. Failures raise; the composite provider swallows them.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import httpx

from .base import Quote

_URL = "https://api.twelvedata.com/quote"
_TIMEOUT = 10.0


class TwelveDataError(Exception):
    pass


class TwelveDataProvider:
    name = "twelvedata"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @staticmethod
    def _map(symbol: str) -> tuple[str, str] | None:
        if symbol.startswith("^"):
            return None
        if symbol.endswith(".NS"):
            return symbol[:-3], "NSE"
        if symbol.endswith(".BO"):
            return symbol[:-3], "BSE"
        return symbol, "NSE"

    async def get_quote(self, symbol: str) -> Quote:
        m = self._map(symbol)
        if m is None:
            raise TwelveDataError(f"no secondary quote for index {symbol}")
        sym, exch = m
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.get(_URL, params={"symbol": sym, "exchange": exch, "apikey": self.api_key})
        except httpx.HTTPError as e:
            raise TwelveDataError(f"network error: {e}") from e
        if r.status_code != 200:
            raise TwelveDataError(f"HTTP {r.status_code}")
        d = r.json()
        if d.get("status") == "error" or "close" not in d:
            raise TwelveDataError(d.get("message", "unusable payload"))
        ts = d.get("timestamp")
        event_time = datetime.fromtimestamp(int(ts), tz=UTC) if ts else datetime.now(UTC)
        return Quote(symbol=symbol, price=float(d["close"]), volume=int(float(d.get("volume") or 0)),
                     event_time=event_time, source=self.name)

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for s in symbols:
            out[s] = await self.get_quote(s)
        return out
