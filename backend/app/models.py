"""API request/response models (the wire contract)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    pin: str


class LoginResponse(BaseModel):
    token: str
    username: str


class AddSymbolRequest(BaseModel):
    symbol: str


class Snapshot(BaseModel):
    price: float
    event_time: datetime


class Change(BaseModel):
    abs: float
    pct: float
    direction: str  # "up" | "down" | "flat"


class Provenance(BaseModel):
    """Every served quote carries where it came from and how fresh it is — the app never shows a
    number without this context. freshness is derived from the quote's event_time age."""

    source: str            # "replay" | "yahoo" | ...
    is_simulated: bool     # true when the data is simulated (Replay) rather than a live feed
    event_time: datetime
    age_seconds: float
    freshness: str         # "fresh" | "delayed" | "stale" | "no_data"


class WatchRow(BaseModel):
    symbol: str
    price: float | None
    provenance: Provenance
    has_baseline: bool
    last_seen: Snapshot | None = None
    change_since_seen: Change | None = None


class ChangeRow(BaseModel):
    symbol: str
    price: float
    provenance: Provenance
    last_seen: Snapshot
    change_since_seen: Change
    # M1 placeholder for the real scoring engine (M4): a naive magnitude flag + reason.
    is_meaningful: bool
    reason: str


class MarketStatusModel(BaseModel):
    is_open: bool
    label: str
    detail: str


class StateResponse(BaseModel):
    """Everything the dashboard needs in one round trip (avoids polling two endpoints)."""

    market: MarketStatusModel
    items: list[WatchRow]
    changes: list[ChangeRow]
