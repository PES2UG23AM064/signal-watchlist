"""API request/response models (the wire contract)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    email: str
    display_name: str | None = None


class MeResponse(BaseModel):
    email: str
    display_name: str | None = None


class AddSymbolRequest(BaseModel):
    symbol: str


class SetQuantityRequest(BaseModel):
    quantity: float | None = Field(default=None, ge=0, le=1e9)  # shares held; None (or 0) clears it


class InjectRequest(BaseModel):
    symbol: str
    kind: str  # "garbage" | "jump" | "future" | "stale"


class Snapshot(BaseModel):
    price: float
    event_time: datetime


class Change(BaseModel):
    abs: float
    pct: float
    direction: str  # "up" | "down" | "flat"


class Provenance(BaseModel):
    """Where a served quote came from and how fresh it is; freshness derives from event_time age."""

    source: str            # "replay" | "yahoo" | ...
    is_simulated: bool
    event_time: datetime
    age_seconds: float
    freshness: str         # "fresh" | "delayed" | "stale" | "no_data"
    quarantined_recent: int = 0  # bad ticks rejected in the last 5 min (stored for audit, never served)
    # A second feed disagrees with the served primary price beyond the threshold; shown, not resolved.
    disputed: bool = False
    dispute: dict | None = None  # {primary_price, primary_source, secondary_price, secondary_source, divergence_pct}


class Explain(BaseModel):
    """The numbers behind a score, for the explainability panel."""

    sigma_move: float          # market-adjusted move in units of this stock's daily sigma (time-scaled)
    move_pct: float            # raw move since you last looked, %
    market_adjusted_pct: float # move minus beta * index move, %
    vol_ratio: float           # today's volume / 20-day average
    crossed: str | None        # "high" | "low" | None  (52-week level crossed since you last looked)
    sigma_daily_pct: float     # the stock's daily volatility, %
    beta: float | None         # vs NIFTY (OLS on real candles)
    elapsed_seconds: float
    # Path since you last looked: a stock that ran +3% and came back to flat still moved.
    peak_pct: float | None = None     # highest point since you looked, % vs then
    trough_pct: float | None = None   # lowest point since you looked, % vs then
    path_note: str | None = None      # e.g. "spiked +2.4% then retraced"


class Signal(BaseModel):
    """Descriptive signal: what was unusual and the numbers behind it. No learned model ships; the
    backtest at /model found no predictive hypothesis that beat noise at this sample size."""

    reasons: list[str]         # threshold-driven; empty => nothing unusual
    is_meaningful: bool        # any reason fired
    unusualness: float         # descriptive ranking score (sigma-equivalent units)
    explain: Explain


class WatchRow(BaseModel):
    symbol: str
    price: float | None
    provenance: Provenance
    has_baseline: bool
    last_seen: Snapshot | None = None
    change_since_seen: Change | None = None
    signal: Signal | None = None   # present when there is a snapshot and baselines
    quantity: float | None = None  # shares held (optional)
    exposure_inr: float | None = None   # quantity * current price
    impact_inr: float | None = None     # quantity * (price now - price when you last looked)
    snoozed_until: datetime | None = None  # held out of "needs attention" until then; still listed


class ChangeRow(BaseModel):
    symbol: str
    price: float
    provenance: Provenance
    last_seen: Snapshot
    change_since_seen: Change
    signal: Signal
    headline: str                  # reasons joined, or the plain % move if nothing unusual fired
    # Co-movement cohorts (presentation only; grouping never hides a row).
    cohort_id: int | None = None
    peer_residual_z: float | None = None  # move vs the median of cohort peers, in sigma of that residual
    moving_alone: bool = False     # |peer_residual_z| >= PEER_Z_FLAG
    quantity: float | None = None  # shares held; held symbols rank by rupees at stake
    impact_inr: float | None = None
    snoozed_until: datetime | None = None  # still listed, but not "needs attention" until this passes


class CohortInfo(BaseModel):
    id: int
    members: list[str]
    mean_corr: float | None        # mean pairwise return correlation among members (None for singletons)


class MarketStatusModel(BaseModel):
    is_open: bool
    label: str
    detail: str


class StateResponse(BaseModel):
    """Everything the dashboard needs in one round trip."""

    market: MarketStatusModel
    items: list[WatchRow]
    changes: list[ChangeRow]       # all moved symbols, ranked; never filtered by the model or by grouping
    cohorts: list[CohortInfo] = []  # multi-member cohorts drive group cards
