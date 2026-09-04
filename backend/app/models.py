"""API request/response models (the wire contract)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
    quantity: float | None  # shares held; None clears it


class InjectRequest(BaseModel):
    """Dev/demo fault injection: make the resilience machinery observable on demand."""

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
    """Every served quote carries where it came from and how fresh it is — the app never shows a
    number without this context. freshness is derived from the quote's event_time age."""

    source: str            # "replay" | "yahoo" | ...
    is_simulated: bool     # true when the data is simulated (Replay) rather than a live feed
    event_time: datetime
    age_seconds: float
    freshness: str         # "fresh" | "delayed" | "stale" | "no_data"
    quarantined_recent: int = 0  # bad ticks rejected in the last 5 min (stored for audit, never served)
    # Cross-source reconciliation: a second feed disagrees with the served (primary) price beyond the
    # threshold. We show the disagreement instead of silently picking one; the primary is what's served.
    disputed: bool = False
    dispute: dict | None = None  # {primary_price, primary_source, secondary_price, secondary_source, divergence_pct}


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
    # Path since you last looked (from the quote ring), because the endpoint can lie: a stock that ran
    # +3% and came back to flat still HAPPENED.
    peak_pct: float | None = None     # highest point since you looked, % vs then
    trough_pct: float | None = None   # lowest point since you looked, % vs then
    path_note: str | None = None      # e.g. "spiked +2.4% then retraced"


class Signal(BaseModel):
    """No learned model ships. The backtest (served at /model) tested three predictive hypotheses on real
    candles and none beat noise at this sample size — so the signal is DESCRIPTIVE: what was unusual, with
    the numbers behind it. (See README: "How 'meaningful' is decided — and what we tested".)"""

    reasons: list[str]         # plain-English, threshold-driven; empty => nothing unusual
    is_meaningful: bool        # = any reason fired (deterministic)
    unusualness: float         # descriptive ranking score (sigma-equivalent units)
    explain: Explain


class WatchRow(BaseModel):
    symbol: str
    price: float | None
    provenance: Provenance
    has_baseline: bool
    last_seen: Snapshot | None = None
    change_since_seen: Change | None = None
    signal: Signal | None = None   # present when we have a snapshot + real baselines
    # Exposure (optional): what you hold, and what this move means in rupees.
    quantity: float | None = None
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
    # Co-movement cohorts (presentation only — every row is still listed; grouping never hides a symbol):
    cohort_id: int | None = None   # which of the user's cohorts this symbol belongs to
    peer_residual_z: float | None = None  # move vs the median of cohort peers, in sigma of that residual
    moving_alone: bool = False     # |peer_residual_z| >= 2: promote — this one is doing something its peers aren't
    # Exposure: if you told us what you hold, we rank held symbols by rupees at stake.
    quantity: float | None = None
    impact_inr: float | None = None
    # Snooze: still listed, but not counted as needing attention until this passes.
    snoozed_until: datetime | None = None


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
