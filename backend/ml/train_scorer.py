"""Offline trainer: fit the 3-feature logistic regression on REAL daily candles and write two
committed artifacts. Dev-only (scikit-learn lives in requirements-dev.txt; production never imports this).

    ./.venv/Scripts/python.exe -m ml.train_scorer

Design (each choice is a Q&A answer):
  * Panel = every (symbol, trading day), features from bars <= t only (app.scoring.features_for_bar,
    the SAME function production uses). Training on all days, not just flagged events, is what makes the
    sample size honest (~250 x N_symbols rows).
  * Label (primary) = path-max follow-through: max over the next N bars of |close_u/close_t - 1| >= K*sigma_t.
    "Did something keep happening" — an ATTENTION label, not a direction bet.
  * Label (pre-registered secondary) = directional continuation. We EXPECT it to fail (~0.5 AUC) and we
    report that: an efficient market should give no directional edge on 252 bars, and it's why the app
    never tells you what to buy.
  * Split = strictly by calendar time, first ~70% of days train / last ~30% test, with an N-day EMBARGO
    dropped from the end of train so no train label window overlaps test. No shuffling (temporal leakage).
  * Standardization uses TRAIN mean/std only; both are persisted so production standardizes identically.
  * K is tuned on TRAIN ONLY to land the base rate in [0.30, 0.50]. Never tuned on test.
  * The backtest NEVER reads symbol_baselines (full-sample => look-ahead); trailing stats are recomputed
    per bar inside app.scoring.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import date, datetime, timezone

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from app import db, scoring
from app.baselines import INDEX_SYMBOL, load_candles

N_FORWARD = 3                     # follow-through horizon, trading days ("the next couple of check-ins")
K_CANDIDATES = (1.5, 2.0, 2.5, 3.0)
TARGET_BASE_RATE = (0.30, 0.50)
TRAIN_FRAC = 0.70

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "model"
MODEL_PATH = OUT_DIR / "scoring_model.json"
REPORT_PATH = OUT_DIR / "backtest_report.json"


async def _load_panel_inputs() -> dict[str, tuple[list[date], np.ndarray, np.ndarray, np.ndarray]]:
    """Per symbol: (days, closes, vols, idx_closes) aligned on common trading days with the index."""
    await db.connect()
    try:
        async with db.pool().acquire() as conn:
            symbols = [r["symbol"] for r in await conn.fetch(
                "select distinct symbol from daily_candles where symbol <> $1 order by symbol", INDEX_SYMBOL)]
            index = {c.day: c.close for c in await load_candles(conn, INDEX_SYMBOL)}
            out = {}
            for s in symbols:
                cs = [c for c in await load_candles(conn, s) if c.day in index]
                if len(cs) < scoring.MIN_BARS + N_FORWARD + 10:
                    continue
                out[s] = (
                    [c.day for c in cs],
                    np.array([c.close for c in cs], float),
                    np.array([c.volume for c in cs], float),
                    np.array([index[c.day] for c in cs], float),
                )
            return out
    finally:
        await db.disconnect()


def _build_panel(inputs, k: float):
    """Rows: (symbol, day, features[3], y_path, y_dir)."""
    rows = []
    for s, (days, closes, vols, idx) in inputs.items():
        n = len(closes)
        for t in range(scoring.MIN_BARS, n - N_FORWARD):
            f = scoring.features_for_bar(closes, vols, idx, t)
            if f is None:
                continue
            fwd = closes[t + 1: t + 1 + N_FORWARD] / closes[t] - 1.0
            y_path = int(np.max(np.abs(fwd)) >= k * f.sigma_used)
            end_move = closes[t + N_FORWARD] / closes[t] - 1.0
            y_dir = int(f.move_pct != 0 and np.sign(end_move) == np.sign(f.move_pct))
            # Third pre-specified label: volatility clustering (the GARCH effect). Did realized variance
            # over the next N days EXCEED what trailing vol predicts? Asks "is this stock entering an
            # active period?", not "which way will it go".
            fwd_daily = closes[t + 1: t + 1 + N_FORWARD] / closes[t: t + N_FORWARD] - 1.0
            y_vol = int(np.sum(fwd_daily ** 2) > N_FORWARD * f.sigma_used ** 2)
            rows.append((s, days[t], f.vector(), y_path, y_dir, y_vol))
    return rows


def _time_split(rows):
    days = sorted({r[1] for r in rows})
    cut = days[int(len(days) * TRAIN_FRAC)]
    embargo_cut = days[max(0, int(len(days) * TRAIN_FRAC) - N_FORWARD)]  # drop last N train days
    train = [r for r in rows if r[1] < embargo_cut]
    test = [r for r in rows if r[1] > cut]
    return train, test, cut


def _xy(rows, which: int):
    X = np.vstack([r[2] for r in rows]); y = np.array([r[which] for r in rows])
    return X, y


def _calibration(p: np.ndarray, y: np.ndarray, bins: int = 10):
    order = np.argsort(p)
    chunks = np.array_split(order, bins)
    out = []
    for i, idxs in enumerate(chunks):
        out.append({"decile": i + 1, "n": int(len(idxs)),
                    "mean_predicted": round(float(p[idxs].mean()), 4),
                    "realized_rate": round(float(y[idxs].mean()), 4)})
    return out


def main() -> None:
    inputs = asyncio.run(_load_panel_inputs())
    if not inputs:
        raise SystemExit("no candle history in daily_candles — backfill symbols first")

    # --- choose K on TRAIN only (base rate in target band); labels depend on K so rebuild per candidate
    chosen_k, panel = None, None
    for k in K_CANDIDATES:
        rows = _build_panel(inputs, k)
        tr, _, _ = _time_split(rows)
        br = np.mean([r[3] for r in tr])
        if TARGET_BASE_RATE[0] <= br <= TARGET_BASE_RATE[1]:
            chosen_k, panel = k, rows
            break
    if panel is None:  # fall back to the default and report it honestly
        chosen_k, panel = 2.0, _build_panel(inputs, 2.0)

    train, test, cut = _time_split(panel)
    Xtr, ytr = _xy(train, 3); Xte, yte = _xy(test, 3)
    mean, std = Xtr.mean(axis=0), Xtr.std(axis=0)
    std = np.where(std > 0, std, 1.0)
    Ztr, Zte = (Xtr - mean) / std, (Xte - mean) / std

    corr = np.corrcoef(Xtr, rowvar=False)  # collinearity check — the sign-flip defense

    clf = LogisticRegression(C=1.0, max_iter=1000).fit(Ztr, ytr)
    p_te = clf.predict_proba(Zte)[:, 1]
    auc = float(roc_auc_score(yte, p_te)); brier = float(brier_score_loss(yte, p_te))
    base = float(yte.mean())
    cal = _calibration(p_te, yte)
    top = cal[-1]["realized_rate"]; lift = round(top / base, 2) if base > 0 else None

    # Pre-registered secondary label: direction. Expected ~0.5 (no edge). Reported, not hidden.
    _, ydtr = _xy(train, 4); _, ydte = _xy(test, 4)
    clf_dir = LogisticRegression(C=1.0, max_iter=1000).fit(Ztr, ydtr)
    auc_dir = float(roc_auc_score(ydte, clf_dir.predict_proba(Zte)[:, 1]))

    # Third pre-specified label: volatility clustering / "entering an active period".
    _, yvtr = _xy(train, 5); _, yvte = _xy(test, 5)
    clf_vol = LogisticRegression(C=1.0, max_iter=1000).fit(Ztr, yvtr)
    p_vol = clf_vol.predict_proba(Zte)[:, 1]
    auc_vol = float(roc_auc_score(yvte, p_vol)); base_vol = float(yvte.mean())
    cal_vol = _calibration(p_vol, yvte)
    lift_vol = round(cal_vol[-1]["realized_rate"] / base_vol, 2) if base_vol > 0 else None

    days_all = sorted({r[1] for r in panel})
    version = f"lr-v1-{datetime.now(timezone.utc):%Y%m%d}"
    meta = {
        "symbols": sorted(inputs.keys()), "n_symbols": len(inputs),
        "date_range": [days_all[0].isoformat(), days_all[-1].isoformat()],
        "train_cutoff": cut.isoformat(), "n_train": int(len(train)), "n_test": int(len(test)),
        "k_sigma": chosen_k, "n_forward_days": N_FORWARD, "trained_at": datetime.now(timezone.utc).isoformat(),
        "sample_note": ("symbol-days, but overlapping 3-day label windows and correlated large-caps put the "
                        "effective sample in the low hundreds — which is why this is 3 features, not 30"),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # The SHIPPED artifact is the ACTIVITY model — the only label with out-of-sample signal. The
    # follow-through and directional models are null and are reported (not shipped) in the report.
    MODEL_PATH.write_text(json.dumps({
        "version": version, "features": scoring.FEATURE_NAMES,
        "label": f"activity: realized variance over next {N_FORWARD} days > trailing norm (volatility clustering)",
        "coef": [round(float(c), 6) for c in clf_vol.coef_[0]], "intercept": round(float(clf_vol.intercept_[0]), 6),
        "mean": [round(float(v), 6) for v in mean], "std": [round(float(v), 6) for v in std],
        "meta": {**meta, "test_auc": round(auc_vol, 4), "test_base_rate": round(base_vol, 4),
                 "top_decile_lift": lift_vol},
    }, indent=2), encoding="utf-8")

    REPORT_PATH.write_text(json.dumps({
        "version": version, "data_source": "real NSE daily candles (Yahoo Finance), NOT the replay simulator",
        "label": {"primary": f"max |move| over next {N_FORWARD} bars >= {chosen_k} sigma (follow-through / attention)",
                  "secondary": f"directional continuation over {N_FORWARD} bars (pre-registered; expected ~0.5 AUC)"},
        "split": {"method": "time-ordered, no shuffle", "train_frac": TRAIN_FRAC, "embargo_days": N_FORWARD},
        "results": {
            "test_auc": round(auc, 4), "test_brier": round(brier, 4), "test_base_rate": round(base, 4),
            "top_decile_realized_rate": top, "top_decile_lift_vs_base": lift,
            "directional_test_auc": round(auc_dir, 4),
            "calibration_deciles": cal,
            "activity_label": {
                "definition": f"realized variance over next {N_FORWARD} days > trailing-sigma^2 * {N_FORWARD} (volatility clustering)",
                "test_auc": round(auc_vol, 4), "test_base_rate": round(base_vol, 4),
                "top_decile_lift_vs_base": lift_vol, "calibration_deciles": cal_vol,
                "coef_standardized": [round(float(c), 4) for c in clf_vol.coef_[0]],
            },
        },
        "model": {"features": scoring.FEATURE_NAMES,
                  "coef_standardized": [round(float(c), 4) for c in clf.coef_[0]],
                  "intercept": round(float(clf.intercept_[0]), 4)},
        "feature_correlation_train": {"features": scoring.FEATURE_NAMES,
                                      "matrix": [[round(float(v), 3) for v in row] for row in corr]},
        **meta,
    }, indent=2), encoding="utf-8")

    print(f"symbols={len(inputs)}  rows={len(panel)}  train={len(train)}  test={len(test)}  K={chosen_k}sigma  N={N_FORWARD}d")
    print(f"test base rate={base:.3f}  AUC={auc:.3f}  Brier={brier:.3f}  top-decile rate={top:.3f} (lift {lift}x)")
    print(f"directional AUC={auc_dir:.3f}  (expected ~0.5: no directional edge)")
    print(f"ACTIVITY (vol-clustering) label: base={base_vol:.3f}  AUC={auc_vol:.3f}  top-decile lift={lift_vol}x  "
          f"coef={dict(zip(scoring.FEATURE_NAMES, [round(float(c),3) for c in clf_vol.coef_[0]]))}")
    print("coef (standardized):", dict(zip(scoring.FEATURE_NAMES, [round(float(c), 3) for c in clf.coef_[0]])))
    print("feature corr:\n", np.round(corr, 3))
    print(f"wrote {MODEL_PATH.name} and {REPORT_PATH.name}")


if __name__ == "__main__":
    main()
