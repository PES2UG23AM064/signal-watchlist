"""Offline backtest: fit a 3-feature logistic regression on real daily candles and write
app/model/backtest_report.json. Dev-only (scikit-learn is in requirements-dev.txt; production never imports this).

    ./.venv/Scripts/python.exe -m ml.train_scorer

Method:
  * Panel = every (symbol, trading day); features from bars <= t only, via app.scoring.features_for_bar,
    the same function production uses. Training on all days (not just flagged events) keeps the sample honest.
  * Primary label = path-max follow-through: max over the next N bars of |close_u/close_t - 1| >= K*sigma_t.
    An attention label ("did something keep happening"), not a direction bet.
  * Pre-registered secondary label = directional continuation, expected to sit at ~0.5 AUC and reported as such.
  * Split strictly by calendar time (first ~70% of days train, rest test), with an N-day embargo dropped from
    the end of train so no train label window overlaps test. No shuffling.
  * Standardization uses train mean/std only. K is tuned on train only to land the base rate in [0.30, 0.50].
  * symbol_baselines is never read (full-sample stats would be look-ahead); trailing stats are recomputed per bar.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import UTC, date, datetime

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from app import db, scoring
from app.baselines import INDEX_SYMBOL, load_candles

N_FORWARD = 3                     # follow-through horizon in trading days
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
    """Rows: (symbol, day, features[3], y_path, y_dir, y_vol)."""
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
            # Third pre-specified label, volatility clustering: does realized variance over the next N days
            # exceed what trailing vol predicts? ("entering an active period", not "which way").
            fwd_daily = closes[t + 1: t + 1 + N_FORWARD] / closes[t: t + N_FORWARD] - 1.0
            y_vol = int(np.sum(fwd_daily ** 2) > N_FORWARD * f.sigma_used ** 2)
            rows.append((s, days[t], f.vector(), y_path, y_dir, y_vol))
    return rows


def _time_split(rows):
    days = sorted({r[1] for r in rows})
    cut = days[int(len(days) * TRAIN_FRAC)]
    embargo_cut = days[max(0, int(len(days) * TRAIN_FRAC) - N_FORWARD)]  # embargo: drop last N train days
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

    # Choose K on train only (base rate in target band); labels depend on K, so rebuild per candidate.
    chosen_k, panel = None, None
    for k in K_CANDIDATES:
        rows = _build_panel(inputs, k)
        tr, _, _ = _time_split(rows)
        br = np.mean([r[3] for r in tr])
        if TARGET_BASE_RATE[0] <= br <= TARGET_BASE_RATE[1]:
            chosen_k, panel = k, rows
            break
    if panel is None:  # no candidate lands in band: fall back to the default K
        chosen_k, panel = 2.0, _build_panel(inputs, 2.0)

    train, test, cut = _time_split(panel)
    Xtr, ytr = _xy(train, 3); Xte, yte = _xy(test, 3)
    mean, std = Xtr.mean(axis=0), Xtr.std(axis=0)
    std = np.where(std > 0, std, 1.0)
    Ztr, Zte = (Xtr - mean) / std, (Xte - mean) / std

    corr = np.corrcoef(Xtr, rowvar=False)  # collinearity check (coefficient sign flips)

    test_days = np.array([r[1] for r in test])

    def auc_ci95(y: np.ndarray, p: np.ndarray, n_boot: int = 2000, seed: int = 0) -> list[float]:
        """Block bootstrap: resample blocks of N_FORWARD consecutive trading days (all symbols of those
        days together), not individual rows. Rows are not independent: stocks share the same market day
        and adjacent days share overlapping label windows, so a row bootstrap would give an interval that
        is too narrow."""
        rng = np.random.default_rng(seed)
        days = np.array(sorted(set(test_days)))
        blocks = [days[i: i + N_FORWARD] for i in range(0, len(days), N_FORWARD)]
        rows_of = {d: np.flatnonzero(test_days == d) for d in days}
        aucs = []
        for _ in range(n_boot):
            picked = rng.integers(0, len(blocks), len(blocks))
            idx = np.concatenate([rows_of[d] for b in picked for d in blocks[b]])
            if y[idx].min() == y[idx].max():
                continue  # a resample with one class has no AUC
            aucs.append(roc_auc_score(y[idx], p[idx]))
        lo, hi = np.percentile(aucs, [2.5, 97.5])
        return [round(float(lo), 4), round(float(hi), 4)]

    def verdict(ci: list[float]) -> str:
        return "no edge: 95% CI includes 0.5" if ci[0] <= 0.5 <= ci[1] else "edge: 95% CI excludes 0.5"

    # Bar for shipping a predictive tag, fixed before looking at the numbers: a statistically non-zero AUC is
    # not enough; the top decile must be right about twice as often as chance.
    MIN_USEFUL_LIFT = 2.0

    clf = LogisticRegression(C=1.0, max_iter=1000).fit(Ztr, ytr)
    p_te = clf.predict_proba(Zte)[:, 1]
    auc = float(roc_auc_score(yte, p_te)); brier = float(brier_score_loss(yte, p_te))
    ci = auc_ci95(yte, p_te)
    base = float(yte.mean())
    cal = _calibration(p_te, yte)
    top = cal[-1]["realized_rate"]; lift = round(top / base, 2) if base > 0 else None

    # Pre-registered secondary label: direction (expected ~0.5, no edge).
    _, ydtr = _xy(train, 4); _, ydte = _xy(test, 4)
    clf_dir = LogisticRegression(C=1.0, max_iter=1000).fit(Ztr, ydtr)
    p_dir = clf_dir.predict_proba(Zte)[:, 1]
    auc_dir = float(roc_auc_score(ydte, p_dir)); ci_dir = auc_ci95(ydte, p_dir)

    # Third pre-specified label: volatility clustering.
    _, yvtr = _xy(train, 5); _, yvte = _xy(test, 5)
    clf_vol = LogisticRegression(C=1.0, max_iter=1000).fit(Ztr, yvtr)
    p_vol = clf_vol.predict_proba(Zte)[:, 1]
    auc_vol = float(roc_auc_score(yvte, p_vol)); base_vol = float(yvte.mean())
    ci_vol = auc_ci95(yvte, p_vol); brier_vol = float(brier_score_loss(yvte, p_vol))
    cal_vol = _calibration(p_vol, yvte)
    lift_vol = round(cal_vol[-1]["realized_rate"] / base_vol, 2) if base_vol > 0 else None

    shippable = [name for name, c, lf in (("follow-through", ci, lift), ("direction", ci_dir, None),
                                          ("activity", ci_vol, lift_vol))
                 if c[0] > 0.5 and lf is not None and lf >= MIN_USEFUL_LIFT]
    conclusion = (
        f"No label clears the bar to ship (block-bootstrap 95% CI excluding 0.5 AND top-decile lift >= {MIN_USEFUL_LIFT}x). "
        f"Follow-through {verdict(ci)}; direction {verdict(ci_dir)}; activity {verdict(ci_vol)} with lift {lift_vol}x. "
        "Therefore NO learned model ships: flags are fixed thresholds and the ranking is descriptive. "
        "This report is the evidence for that decision."
        if not shippable else
        f"{', '.join(shippable)} clears the pre-stated bar — re-examine before deciding whether anything should ship."
    )

    days_all = sorted({r[1] for r in panel})
    version = f"backtest-v2-{datetime.now(UTC):%Y%m%d}"
    meta = {
        "symbols": sorted(inputs.keys()), "n_symbols": len(inputs),
        "date_range": [days_all[0].isoformat(), days_all[-1].isoformat()],
        "train_cutoff": cut.isoformat(), "n_train": int(len(train)), "n_test": int(len(test)),
        "k_sigma": chosen_k, "n_forward_days": N_FORWARD, "trained_at": datetime.now(UTC).isoformat(),
        "sample_note": ("symbol-days, but overlapping 3-day label windows and correlated large-caps put the "
                        "effective sample in the low hundreds — which is why this is 3 features, not 30"),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # No runtime model is written (see `conclusion`); remove any stale artifact so production cannot pick it up.
    if MODEL_PATH.exists():
        MODEL_PATH.unlink()

    REPORT_PATH.write_text(json.dumps({
        "version": version, "data_source": "real NSE daily candles (Yahoo Finance), NOT the replay simulator",
        "ships_a_model": False,
        "label": {"primary": f"max |move| over next {N_FORWARD} bars >= {chosen_k} sigma (follow-through / attention)",
                  "secondary": f"directional continuation over {N_FORWARD} bars (pre-registered; expected ~0.5 AUC)",
                  "activity": f"realized variance over next {N_FORWARD} days > trailing-sigma^2 * {N_FORWARD} (volatility clustering)"},
        "split": {"method": "time-ordered, no shuffle", "train_frac": TRAIN_FRAC, "embargo_days": N_FORWARD,
                  "ci_method": f"block bootstrap over {N_FORWARD}-day blocks of test days (all symbols of a day together), "
                               "2000 resamples, percentile 95% interval",
                  "ship_bar": f"CI excludes 0.5 AND top-decile lift >= {MIN_USEFUL_LIFT}x"},
        "results": {
            "conclusion": conclusion,
            "test_auc": round(auc, 4), "test_auc_ci95": ci, "verdict": verdict(ci),
            "test_brier": round(brier, 4), "test_base_rate": round(base, 4),
            "top_decile_realized_rate": top, "top_decile_lift_vs_base": lift,
            "directional_test_auc": round(auc_dir, 4), "directional_test_auc_ci95": ci_dir,
            "directional_verdict": verdict(ci_dir),
            "calibration_deciles": cal,
            "activity_label": {
                "definition": f"realized variance over next {N_FORWARD} days > trailing-sigma^2 * {N_FORWARD} (volatility clustering)",
                "test_auc": round(auc_vol, 4), "test_auc_ci95": ci_vol, "verdict": verdict(ci_vol),
                "test_brier": round(brier_vol, 4), "test_base_rate": round(base_vol, 4),
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
    print(f"follow-through: base={base:.3f}  AUC={auc:.3f} CI95={ci}  Brier={brier:.3f}  top-decile lift={lift}x  -> {verdict(ci)}")
    print(f"direction:      AUC={auc_dir:.3f} CI95={ci_dir}  -> {verdict(ci_dir)}")
    print(f"activity:       base={base_vol:.3f}  AUC={auc_vol:.3f} CI95={ci_vol}  Brier={brier_vol:.3f}  lift={lift_vol}x  -> {verdict(ci_vol)}")
    print("coef (standardized, follow-through):", dict(zip(scoring.FEATURE_NAMES, [round(float(c), 3) for c in clf.coef_[0]], strict=False)))
    print("feature corr:\n", np.round(corr, 3))
    print(conclusion)
    print(f"wrote {REPORT_PATH.name} (no runtime model artifact)")


if __name__ == "__main__":
    main()
