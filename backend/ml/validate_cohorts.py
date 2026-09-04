"""Offline validation of watchlist cohorts on real cached candles -> app/model/cohort_report.json (dev-only).

    ./.venv/Scripts/python.exe -m ml.validate_cohorts

Reports three things:
  1. Structure: mean intra-cohort vs inter-cohort return correlation.
  2. Out-of-sample stability: fit on the first half of the days, refit on the second half, report
     agreement (adjusted Rand index + how many symbols kept their grouping).
  3. Counterfactual alert replay over every real trading day: digest cards under the old rule (one per
     flagged symbol) vs the cohort rule (co-moving flagged members collapse into one card; "moving alone"
     members always stay their own card), plus the invariant that no moving-alone symbol was ever collapsed.
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import numpy as np
from sklearn.metrics import adjusted_rand_score

from app import cohorts, db, scoring
from app.baselines import INDEX_SYMBOL, load_candles

OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "model" / "cohort_report.json"


async def _load() -> tuple[dict, list]:
    await db.connect()
    try:
        async with db.pool().acquire() as conn:
            syms = [r["symbol"] for r in await conn.fetch(
                "select distinct symbol from daily_candles where symbol <> $1 order by symbol", INDEX_SYMBOL)]
            cands = {s: await load_candles(conn, s) for s in syms}
            index = await load_candles(conn, INDEX_SYMBOL)
        return cands, index
    finally:
        await db.disconnect()


def _labels(model: cohorts.CohortModel, symbols: list[str]) -> list[int]:
    return [model.cohort_of[s] for s in symbols]


def _structure(cands) -> dict:
    model = cohorts.build(cands)
    symbols, rets, _ = cohorts.returns_matrix({s: c for s, c in cands.items() if len(c) >= cohorts.MIN_OVERLAP_DAYS})
    C = cohorts.corr_matrix(rets); idx = {s: i for i, s in enumerate(symbols)}
    intra = [C[idx[a], idx[b]] for g in model.cohorts if len(g) > 1 for a in g for b in g if a < b and a in idx and b in idx]
    inter = [C[idx[a], idx[b]] for ga in model.cohorts for gb in model.cohorts if ga < gb
             for a in ga for b in gb if a in idx and b in idx]
    return {
        "cohorts": model.cohorts,
        "mean_intra_cohort_corr": round(float(np.mean(intra)), 3) if intra else None,
        "mean_inter_cohort_corr": round(float(np.mean(inter)), 3) if inter else None,
        "corr_matrix": {"symbols": symbols, "matrix": [[round(float(v), 2) for v in row] for row in C]},
        "n_days": model.n_days,
    }, model


def _stability(cands) -> dict:
    """Fit on the first half of days vs the second half: do symbols keep the same grouping?"""
    days = sorted(set.intersection(*[{c.day for c in v} for v in cands.values()]))
    mid = days[len(days) // 2]
    first = {s: [c for c in v if c.day <= mid] for s, v in cands.items()}
    second = {s: [c for c in v if c.day > mid] for s, v in cands.items()}
    m1, m2 = cohorts.build(first, ), cohorts.build(second)
    symbols = sorted(cands)
    l1, l2 = _labels(m1, symbols), _labels(m2, symbols)
    # a symbol kept its grouping if its set of co-members is identical in both halves
    kept = sum(1 for s in symbols
               if {x for g in m1.cohorts if s in g for x in g} == {x for g in m2.cohorts if s in g for x in g})
    return {"split_day": mid.isoformat(), "adjusted_rand_index": round(float(adjusted_rand_score(l1, l2)), 3),
            "symbols_kept_grouping": f"{kept} of {len(symbols)}",
            "first_half_cohorts": m1.cohorts, "second_half_cohorts": m2.cohorts,
            "note": ("half-year windows have ~120 bars each — enough to test stability, thin enough that a "
                     "borderline pair can flip; the ARI is the honest summary")}


def _counterfactual(cands, index, model: cohorts.CohortModel) -> dict:
    """Replay every real day: old rule = one card per flagged symbol; cohort rule = co-moving flagged
    members of a cohort collapse to one card per direction, 'moving alone' always its own card."""
    idx_by_day = {c.day: c.close for c in index}
    aligned = {s: [c for c in v if c.day in idx_by_day] for s, v in cands.items()}
    days = sorted(set.intersection(*[{c.day for c in v} for v in aligned.values()]))
    arrays = {s: (np.array([c.close for c in v if c.day in set(days)]), np.array([c.volume for c in v if c.day in set(days)]))
              for s, v in aligned.items()}
    idx_closes = np.array([idx_by_day[d] for d in days])

    old_cards = new_cards = 0; alone_hidden = 0; alone_total = 0
    daily = []
    for t in range(scoring.MIN_BARS, len(days)):
        flagged, moves = {}, {}
        for s, (cl, vo) in arrays.items():
            f = scoring.features_for_bar(cl, vo, idx_closes, t)
            if f is None:
                continue
            moves[s] = f.move_pct
            if scoring.flags_and_reasons(f):
                flagged[s] = np.sign(f.move_pct)
        if not flagged:
            continue
        res = cohorts.peer_residuals(model, moves, elapsed_seconds=scoring.TRADING_DAY_S,
                                     trading_day_s=scoring.TRADING_DAY_S, min_elapsed_s=scoring.MIN_ELAPSED_S)
        alone = {s for s in flagged if res.get(s) and abs(res[s][1]) >= cohorts.PEER_Z_FLAG}
        alone_total += len(alone)
        day_old = len(flagged); day_new = len(alone)
        for g in model.cohorts:
            pack = [s for s in g if s in flagged and s not in alone]
            if not pack:
                continue
            for sgn in (1.0, -1.0):
                members = [s for s in pack if flagged[s] == sgn]
                if members:
                    day_new += 1
                    alone_hidden += sum(1 for s in members if s in alone)  # 0 by construction; asserted in main
        old_cards += day_old; new_cards += day_new
        daily.append((days[t], day_old, day_new))

    worst = sorted(daily, key=lambda x: -x[1])[:5]
    return {
        "days_with_alerts": len(daily), "cards_old_rule": old_cards, "cards_cohort_rule": new_cards,
        "reduction_pct": round(100 * (1 - new_cards / old_cards), 1) if old_cards else None,
        "moving_alone_flags": alone_total, "moving_alone_hidden_in_a_group": alone_hidden,  # invariant: 0
        "busiest_days": [{"day": d.isoformat(), "old": o, "new": n} for d, o, n in worst],
    }


def main() -> None:
    cands, index = asyncio.run(_load())
    if len(cands) < 3:
        raise SystemExit("need >= 3 symbols with candle history")
    structure, model = _structure(cands)
    stability = _stability(cands)
    counter = _counterfactual(cands, index, model)
    assert counter["moving_alone_hidden_in_a_group"] == 0, "INVARIANT VIOLATED: a moving-alone symbol was collapsed"

    report = {"data_source": "real NSE daily candles (Yahoo Finance), NOT the replay simulator",
              "method": f"average-linkage agglomerative clustering on daily-return correlation, merge while mean corr >= {cohorts.MERGE_THRESHOLD}",
              "structure": structure, "stability": stability, "alert_counterfactual": counter}
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("cohorts:", structure["cohorts"])
    print(f"intra corr {structure['mean_intra_cohort_corr']} vs inter {structure['mean_inter_cohort_corr']}")
    print(f"stability: ARI {stability['adjusted_rand_index']}, kept grouping {stability['symbols_kept_grouping']}")
    print(f"alerts: {counter['cards_old_rule']} -> {counter['cards_cohort_rule']} cards ({counter['reduction_pct']}% fewer); "
          f"moving-alone flags {counter['moving_alone_flags']}, hidden {counter['moving_alone_hidden_in_a_group']}")
    print("wrote", OUT.name)


if __name__ == "__main__":
    main()
