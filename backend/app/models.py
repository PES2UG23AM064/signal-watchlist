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


class Explain(BaseModel):
    """The numbers behind a score — the explainability panel. Nothing here is a black box."""

    sigma_move: float          # market-adjusted move in units of this stock's own daily sigma (time-scaled)
    move_pct: float            # raw move since you last looked, %
    market_adjusted_pct: float # move minus beta * index move, %
    vol_ratio: float           # today's volume / 20-day average
    crossed: str | None        # "high" | "low" | None  (52-week level crossed since you last looked)
    sigma_daily_pct: float     # the stock's real daily volatility, %
    beta: float | None         # vs NIFTY (OLS on real candles)
    elapsed_seconds: float


class Activity(BaseModel):
    """The one learned signal: P(entering an active period). Secondary tag, never the ranking."""

    probability: float
    version: str


class Signal(BaseModel):
    reasons: list[str]         # plain-English, threshold-driven; empty => nothing unusual
    is_meaningful: bool        # = any reason fired (deterministic; the model never gates this)
    unusualness: float         # descriptive ranking score (sigma-equivalent units)
    explain: Explain
    activity: Activity | None = None


class WatchRow(BaseModel):
    symbol: str
    price: float | None
    provenance: Provenance
    has_baseline: bool
    last_seen: Snapshot | None = None
    change_since_seen: Change | None = None
    signal: Signal | None = None   # present when we have a snapshot + real baselines


class ChangeRow(BaseModel):
    symbol: str
    price: float
    provenance: Provenance
    last_seen: Snapshot
    change_since_seen: Change
    signal: Signal
    headline: str                  # reasons joined, or the plain % move if nothing unusual fired
    # Co-movement cohorts (presentation only — every row is still listed; grouping never hides a symbol):
    cohort_id: int | None = None   # which of the user's cohorts this symbol belongs to
    peer_residual_z: float | None = None  # move vs the median of cohort peers, in sigma of that residual
    moving_alone: bool = False     # |peer_residual_z| >= 2: promote — this one is doing something its peers aren't


class CohortInfo(BaseModel):
    """One co-movement cohort in the user's watchlist, for the client to collapse co-movers into a card."""

    id: int
    members: list[str]
    mean_corr: float | None        # mean pairwise return correlation among members (None for singletons)


class MarketStatusModel(BaseModel):
    is_open: bool
    label: str
    detail: str


class StateResponse(BaseModel):
    """Everything the dashboard needs in one round trip (avoids polling two endpoints)."""

    market: MarketStatusModel
    items: list[WatchRow]
    changes: list[ChangeRow]       # ALL moved symbols, ranked (never filtered by the model or by grouping)
    cohorts: list[CohortInfo] = []  # the user's co-movement cohorts (multi-member ones drive group cards)
