"""Provider interface + the Quote domain model.

A Quote always carries its provenance: the value, the *event time* it is as-of (exchange/sample
time, used for last-write-wins ordering), when we fetched it, and which source produced it. Later
milestones lean on these fields for stale-write rejection and conflict reconciliation.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class Quote(BaseModel):
    """A raw quote as produced by a provider. Intentionally permissive — a provider may hand us
    garbage (0, negative, an absurd jump), and we must NOT crash on it. Sanity checks live at the
    ingestion boundary (`app.quotes.sanitize`), which flags a quote as suspect and quarantines it
    rather than raising. Never validate-to-raise here."""

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
