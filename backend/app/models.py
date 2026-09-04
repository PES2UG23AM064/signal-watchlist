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


class WatchRow(BaseModel):
    symbol: str
    price: float
    event_time: datetime
    source: str
    has_baseline: bool
    last_seen: Snapshot | None = None
    change_since_seen: Change | None = None


class ChangeRow(BaseModel):
    symbol: str
    price: float
    event_time: datetime
    last_seen: Snapshot
    change_since_seen: Change
    # M1 placeholder for the real scoring engine (M4): a naive magnitude flag + reason.
    is_meaningful: bool
    reason: str
