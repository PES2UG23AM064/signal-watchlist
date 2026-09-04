"""Provider interface and the Quote domain model.

A Quote carries its event time (exchange/sample time, used for last-write-wins ordering) and source.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class Quote(BaseModel):
    """A raw quote as produced by a provider. Intentionally permissive: sanity checks live at the
    ingestion boundary (app.quotes.is_suspect), which quarantines rather than raises."""

    symbol: str
    price: float
    volume: int
    event_time: datetime
    source: str


class MarketDataProvider(Protocol):
    """Anything that can produce current quotes for symbols."""

    name: str

    async def get_quote(self, symbol: str) -> Quote: ...

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]: ...
