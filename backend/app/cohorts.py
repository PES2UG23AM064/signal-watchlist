"""Watchlist co-movement cohorts: cluster a user's symbols by daily-return correlation.

Correlation is on returns, never price levels, and only on cached real candles (never Replay).
Cohorts moving together collapse into one group card; a member far from its peers is "moving alone".
Grouping changes presentation only: every member keeps its own row and reasons, nothing is hidden.
Computed at runtime per watchlist; O(n^3) average-linkage clustering is fine at watchlist sizes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from .providers.yahoo import Candle

MIN_OVERLAP_DAYS = 120      # below this a symbol is a singleton cohort (new listing, thin history)
MERGE_THRESHOLD = 0.5       # NSE large-cap baseline pairwise corr is ~0.3-0.4; 0.5 is real co-movement
PEER_Z_FLAG = 2.0           # |peer-residual z| >= this => "moving alone"


@dataclass(frozen=True)
class CohortModel:
    symbols: list[str]                 # aligned order
    corr: np.ndarray                   # pairwise correlation of daily returns
    cohorts: list[list[str]]           # clusters (singletons included)
    cohort_of: dict[str, int]          # symbol -> cohort index
    sigma_resid: dict[str, float]      # daily stdev of (own return - median peer return); absent for singletons
    n_days: int


# --------------------------------------------------------------------------- returns & correlation

def returns_matrix(candles_by_symbol: dict[str, list[Candle]]) -> tuple[list[str], np.ndarray, list[date]]:
    """Align on common trading days (holiday calendars differ) and return daily simple returns,
    shape (n_days - 1, n_symbols)."""
    symbols = sorted(candles_by_symbol)
    if not symbols:
        return [], np.zeros((0, 0)), []
    day_sets = [{c.day for c in candles_by_symbol[s]} for s in symbols]
    common = sorted(set.intersection(*day_sets))
    if len(common) < 2:
        return symbols, np.zeros((0, len(symbols))), common
    by_day = {s: {c.day: c.close for c in candles_by_symbol[s]} for s in symbols}
    closes = np.array([[by_day[s][d] for s in symbols] for d in common])
    rets = closes[1:] / closes[:-1] - 1.0
    return symbols, rets, common


def corr_matrix(rets: np.ndarray) -> np.ndarray:
    if rets.shape[0] < 2 or rets.shape[1] == 0:
        return np.eye(rets.shape[1]) if rets.shape[1] else np.zeros((0, 0))
    c = np.corrcoef(rets, rowvar=False)
    c = np.nan_to_num(np.atleast_2d(c), nan=0.0)
    np.fill_diagonal(c, 1.0)
    return c


# --------------------------------------------------------------------------- clustering

def cluster(corr: np.ndarray, thresh: float = MERGE_THRESHOLD) -> list[list[int]]:
    """Average-linkage agglomerative clustering: merge the two groups with the highest mean pairwise
    correlation while that mean is >= thresh."""
    n = corr.shape[0]
    groups: list[list[int]] = [[i] for i in range(n)]
    while len(groups) > 1:
        best_score, best_a, best_b = -2.0, -1, -1
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                score = float(np.mean([corr[i, j] for i in groups[a] for j in groups[b]]))
                if score > best_score:
                    best_score, best_a, best_b = score, a, b
        if best_score < thresh:
            break
        groups[best_a] = groups[best_a] + groups[best_b]
        groups.pop(best_b)
    return [sorted(g) for g in groups]


def build(candles_by_symbol: dict[str, list[Candle]], thresh: float = MERGE_THRESHOLD) -> CohortModel:
    """Full pipeline over cached candles. Symbols with < MIN_OVERLAP_DAYS of common history become
    singletons rather than being clustered on thin evidence."""
    # Drop thin-history symbols first so they cannot shrink the common window for everyone else.
    ok = {s: c for s, c in candles_by_symbol.items() if len(c) >= MIN_OVERLAP_DAYS}
    thin = [s for s in candles_by_symbol if s not in ok]
    symbols, rets, days = returns_matrix(ok)
    if len(days) - 1 < MIN_OVERLAP_DAYS:   # not enough overlap even among the "ok" set
        thin, symbols, rets = sorted(candles_by_symbol), [], np.zeros((0, 0))
    corr = corr_matrix(rets) if symbols else np.zeros((0, 0))
    groups = cluster(corr, thresh) if symbols else []
    cohorts = [[symbols[i] for i in g] for g in groups] + [[s] for s in thin]
    cohort_of = {s: k for k, g in enumerate(cohorts) for s in g}

    # Per-symbol stdev of the peer residual (own return - median of cohort peers' returns).
    sigma_resid: dict[str, float] = {}
    idx = {s: i for i, s in enumerate(symbols)}
    for g in cohorts:
        if len(g) < 2 or any(s not in idx for s in g):
            continue
        cols = [idx[s] for s in g]
        for s in g:
            own = rets[:, idx[s]]
            peers = np.median(rets[:, [c for c in cols if c != idx[s]]], axis=1)
            sigma_resid[s] = float(np.std(own - peers, ddof=1))
    # `symbols` must be the corr-aligned list from returns_matrix: callers index `corr` by position in it.
    return CohortModel(symbols=symbols, corr=corr, cohorts=cohorts, cohort_of=cohort_of,
                       sigma_resid=sigma_resid, n_days=max(0, len(days) - 1))


# --------------------------------------------------------------------------- live application

def peer_residuals(model: CohortModel, moves: dict[str, float], elapsed_seconds: float,
                   trading_day_s: float, min_elapsed_s: float) -> dict[str, tuple[float, float] | None]:
    """For each symbol with live move `moves[s]` (fraction), return (residual_move, residual_z) vs the
    median move of its cohort peers, z-scaled by sigma_resid * sqrt(elapsed). Singletons -> None
    (the caller falls back to the NIFTY-beta residual)."""
    elapsed_days = max(elapsed_seconds, min_elapsed_s) / trading_day_s
    out: dict[str, tuple[float, float] | None] = {}
    for g in model.cohorts:
        present = [s for s in g if s in moves]
        for s in present:
            peers = [moves[p] for p in present if p != s]
            sig = model.sigma_resid.get(s)
            if not peers or not sig or sig <= 0:
                out[s] = None
                continue
            resid = float(moves[s] - float(np.median(peers)))
            # Plain Python floats: these flow into Pydantic/JSON and numpy scalars are not serializable.
            out[s] = (resid, float(resid / (sig * float(np.sqrt(elapsed_days)))))
    return out
