"""Meaningfulness scoring — deterministic features + flags/reasons, and a LEARNED ordering.

Two paths share ONE feature definition (this is what makes "the exact same scoring function runs in
the backtest and in production" true rather than a claim):
  * bar path  (backtest / training): features for day t from bars <= t ONLY. No look-ahead.
  * live path (the digest): the same 3 features on the since-you-last-looked window, with the move
    normalized by sigma * sqrt(elapsed) — square-root-of-time scaling, a stated assumption.

The 3 features (kept deliberately few: the effective sample is in the low hundreds):
  abs_resid_z    |move - beta*index_move| / sigma  -> market-ADJUSTED move in sigma units. (Raw z is
                 dropped on purpose: it correlates ~0.9 with this and collinear features flip signs.)
  log_vol_ratio  log(volume / trailing-20d mean volume)
  cross_flag     1 if price broke the trailing 52w high/low (reference EXCLUDES the current bar)

The model (logistic regression, fit OFFLINE in ml/train_scorer.py, shipped as model/scoring_model.json)
only ORDERS: it maps features -> follow-through probability, used as priority. It never creates or
suppresses a flag; every deterministic flag still renders with its plain-English reason. If the artifact
is missing we fall back to transparent heuristic weights and label the result 'heuristic'.
"""
from __future__ import annotations

import json
import math
import pathlib
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

LOOKBACK = 252          # trailing window for sigma / beta / 52w reference
MIN_BARS = 60           # need this much history before a bar is scorable
VOL_WINDOW = 20

# Deterministic flag thresholds -> the "reasons". The model does NOT change these.
Z_FLAG = 2.0            # >= 2 sigma market-adjusted move
VOL_FLAG = 1.5          # >= 1.5x normal volume

# Live-path time scaling. NSE session = 6h15m. Below ~15 min the sqrt-time scaling is not meaningful.
TRADING_DAY_S = 6.25 * 3600
MIN_ELAPSED_S = 15 * 60

FEATURE_NAMES = ["abs_resid_z", "log_vol_ratio", "cross_flag"]
MODEL_PATH = pathlib.Path(__file__).resolve().parent / "model" / "scoring_model.json"


@dataclass(frozen=True)
class Trailing:
    sigma: float      # stdev of daily returns, bars <= t
    beta: float       # OLS slope vs index, bars <= t
    avg_vol20: float  # mean volume, last 20 bars incl. t
    hi: float         # trailing high EXCLUDING bar t
    lo: float         # trailing low  EXCLUDING bar t


@dataclass(frozen=True)
class Features:
    # model inputs
    abs_resid_z: float
    log_vol_ratio: float
    cross_flag: float
    # raw inputs kept for the reason strings / explainability panel
    resid_pct: float      # market-adjusted move, fraction (signed)
    move_pct: float       # raw move, fraction (signed)
    vol_ratio: float
    crossed: str | None   # "high" | "low" | None
    sigma_used: float     # the denominator actually used (daily sigma, or sigma*sqrt(elapsed) live)

    def vector(self) -> np.ndarray:
        return np.array([self.abs_resid_z, self.log_vol_ratio, self.cross_flag], dtype=float)


# --------------------------------------------------------------------------- bar path (no look-ahead)

def trailing_baselines(closes: np.ndarray, vols: np.ndarray, idx_closes: np.ndarray, t: int) -> Trailing:
    """Baselines for bar t computed from bars <= t ONLY. hi/lo exclude bar t itself so that a 'cross'
    is a genuine break of the prior range, not the bar being its own reference."""
    start = max(0, t - LOOKBACK + 1)           # first bar in the window
    c = closes[max(0, start - 1): t + 1]       # one extra bar so we get `window` returns
    rets = c[1:] / c[:-1] - 1.0
    sigma = float(np.std(rets, ddof=1)) if rets.size >= 2 else float("nan")

    ic = idx_closes[max(0, start - 1): t + 1]
    irets = ic[1:] / ic[:-1] - 1.0
    beta = 1.0
    if irets.size >= 2 and rets.size == irets.size:
        var_m = float(np.var(irets, ddof=1))
        if var_m > 0:
            beta = float(np.cov(rets, irets, ddof=1)[0, 1] / var_m)

    avg_vol20 = float(np.mean(vols[max(0, t - VOL_WINDOW + 1): t + 1]))
    ref = closes[start:t]                      # EXCLUDES t
    hi = float(ref.max()) if ref.size else float(closes[t])
    lo = float(ref.min()) if ref.size else float(closes[t])
    return Trailing(sigma=sigma, beta=beta, avg_vol20=avg_vol20, hi=hi, lo=lo)


def features_for_bar(closes: np.ndarray, vols: np.ndarray, idx_closes: np.ndarray, t: int) -> Features | None:
    """Features for day t using bars <= t only. None if not enough history / degenerate sigma."""
    if t < MIN_BARS or t >= len(closes):
        return None
    tb = trailing_baselines(closes, vols, idx_closes, t)
    if not (tb.sigma > 0) or math.isnan(tb.sigma):
        return None
    move = float(closes[t] / closes[t - 1] - 1.0)
    idx_move = float(idx_closes[t] / idx_closes[t - 1] - 1.0)
    return _assemble(move, idx_move, tb.beta, tb.sigma, float(vols[t]), tb.avg_vol20,
                     float(closes[t]), float(closes[t - 1]), tb.hi, tb.lo)


# --------------------------------------------------------------------------- live path (since last seen)

def live_features(
    price_now: float, price_seen: float,
    idx_now: float | None, idx_seen: float | None,
    elapsed_seconds: float,
    sigma_daily: float, beta: float | None, avg_vol20: float, vol_today: float,
    hi52: float, lo52: float,
) -> Features | None:
    """Same 3 features on the since-you-last-looked window. The move is normalized by
    sigma_daily * sqrt(elapsed_trading_days): a 4-hour move and a 3-day move land on one scale.
    (Assumes sqrt-time scaling holds intraday — approximately true; stated as a limitation.)"""
    if price_seen <= 0 or not (sigma_daily > 0):
        return None
    elapsed_days = max(elapsed_seconds, MIN_ELAPSED_S) / TRADING_DAY_S
    sigma_eff = sigma_daily * math.sqrt(elapsed_days)
    move = price_now / price_seen - 1.0
    if idx_now and idx_seen and idx_seen > 0 and beta is not None:
        idx_move = idx_now / idx_seen - 1.0
        b = beta
    else:
        idx_move, b = 0.0, 0.0   # no index context -> no market adjustment (honest degradation)
    return _assemble(move, idx_move, b, sigma_eff, vol_today, avg_vol20, price_now, price_seen, hi52, lo52)


def _assemble(move, idx_move, beta, sigma, vol, avg_vol, price_now, price_ref, hi, lo) -> Features:
    resid = move - beta * idx_move
    vol_ratio = (vol / avg_vol) if (avg_vol > 0 and vol > 0) else 1.0
    crossed = "high" if (price_ref <= hi < price_now) else "low" if (price_ref >= lo > price_now) else None
    return Features(
        abs_resid_z=abs(resid) / sigma,
        log_vol_ratio=math.log(vol_ratio),
        cross_flag=1.0 if crossed else 0.0,
        resid_pct=resid, move_pct=move, vol_ratio=vol_ratio, crossed=crossed, sigma_used=sigma,
    )


# --------------------------------------------------------------------------- flags & reasons (deterministic)

def flags_and_reasons(f: Features) -> list[str]:
    """Plain-English reasons. Purely threshold-driven; the model never touches these."""
    reasons: list[str] = []
    if f.abs_resid_z >= Z_FLAG:
        reasons.append(f"{f.abs_resid_z:.1f}σ move vs its own volatility, market-adjusted")
    if f.vol_ratio >= VOL_FLAG:
        reasons.append(f"{f.vol_ratio:.1f}× normal volume")
    if f.crossed == "high":
        reasons.append("crossed its 52-week high")
    elif f.crossed == "low":
        reasons.append("crossed its 52-week low")
    return reasons


# --------------------------------------------------------------------------- ordering (descriptive, by design)

def unusualness(f: Features) -> float:
    """PRIORITY for the digest: a DESCRIPTIVE measure of how unusual what already happened was, in
    roughly sigma-equivalent units — market-adjusted move (in sigma) + excess log-volume + a 52w break.

    Weights are a deliberate, transparent 1:1:1. The backtest (ml/train_scorer.py) tested whether these
    features PREDICT follow-through on a year of real NSE candles and found no edge (AUC ~0.5), so we
    do not pretend to have learned asymmetric predictive weights, and we never present this as a
    forecast. It ranks what was unusual; it does not predict what comes next."""
    return f.abs_resid_z + max(f.log_vol_ratio, 0.0) + f.cross_flag


# --------------------------------------------------------------------------- the one learned signal

@lru_cache(maxsize=1)
def load_model() -> dict | None:
    """The committed artifact from ml/train_scorer.py (the ACTIVITY model); None -> no outlook tag."""
    try:
        m = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        if m.get("features") != FEATURE_NAMES:
            return None
        return m
    except (OSError, ValueError):
        return None


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def activity_outlook(f: Features) -> tuple[float, str] | None:
    """The ONE learned signal the real data supports: probability this stock is entering an ACTIVE
    period (realized variance over the next few days exceeding its trailing norm — volatility
    clustering). Fit offline on real candles; weak but genuine (test AUC ~0.54, top-decile lift ~1.5x),
    driven almost entirely by relative volume. A secondary tag: never the ranking, never a direction
    call. Runtime is standardize -> dot -> sigmoid; no sklearn. None if the artifact is absent."""
    m = load_model()
    if m is None:
        return None
    mean, std = np.array(m["mean"]), np.array(m["std"])
    std = np.where(std > 0, std, 1.0)
    z = float(np.dot((f.vector() - mean) / std, np.array(m["coef"])) + m["intercept"])
    return _sigmoid(z), m["version"]
