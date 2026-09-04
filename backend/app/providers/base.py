"""Provider interface + the Quote domain model.

A Quote always carries its provenance: the value, the *event time* it is as-of (exchange/sample
time, used for last-write-wins ordering), when we fetched it, and which source produced it. Later
milestones lean on these fields for stale-write rejection and conflict reconciliation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from pydantic import BaseModel, field_validator


class Quote(BaseModel):
    symbol: str
    price: float
    volume: int
    event_time: datetime
    source: str

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: float) -> float:
        # The integrity boundary: a non-positive price is never a real quote.
        if v <= 0:
            raise ValueError("price must be > 0")
        return v


class MarketDataProvider(Protocol):
    """Anything that can produce current quotes for symbols."""

    name: str

    async def get_quote(self, symbol: str) -> Quote: ...

    async def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]: ...
