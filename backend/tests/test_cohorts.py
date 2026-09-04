"""Cohort invariants: clusters recover known structure; grouping never hides a symbol; thin history ->
singleton; misaligned calendars are aligned, not silently misused."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from app import cohorts
from app.providers.yahoo import Candle


def _candles(closes, start=date(2025, 1, 1), skip_days=()):
    out, d = [], start
    for i, c in enumerate(closes):
        while d.weekday() >= 5 or d in skip_days:
            d += timedelta(days=1)
        out.append(Candle(day=d, open=c, high=c, low=c, close=float(c), volume=1_000_000))
        d += timedelta(days=1)
    return out


def _sector_series(n=260, seed=1):
    """Two sectors of 3 names each with a shared factor, plus one independent name."""
    rng = np.random.default_rng(seed)
    f_it, f_bank = rng.normal(0, 0.012, n), rng.normal(0, 0.012, n)
    def walk(factor, w, noise=0.006):
        r = w * factor + rng.normal(0, noise, n)
        return 1000 * np.exp(np.cumsum(r))
    return {
        "TCS.NS": walk(f_it, 1.0), "INFY.NS": walk(f_it, 1.0), "WIPRO.NS": walk(f_it, 0.9),
        "HDFCBANK.NS": walk(f_bank, 1.0), "ICICIBANK.NS": walk(f_bank, 1.0), "SBIN.NS": walk(f_bank, 0.9),
        "LONER.NS": 1000 * np.exp(np.cumsum(rng.normal(0, 0.015, n))),
    }


def test_recovers_sector_structure_from_returns_alone():
    series = _sector_series()
    model = cohorts.build({s: _candles(v) for s, v in series.items()})
    by_symbol = {s: frozenset(g) for g in model.cohorts for s in g}
    assert by_symbol["TCS.NS"] == by_symbol["INFY.NS"] == by_symbol["WIPRO.NS"]
    assert by_symbol["HDFCBANK.NS"] == by_symbol["ICICIBANK.NS"] == by_symbol["SBIN.NS"]
    assert by_symbol["TCS.NS"] != by_symbol["HDFCBANK.NS"]
    assert by_symbol["LONER.NS"] == frozenset({"LONER.NS"})   # independent name stays alone
    # Every symbol appears in exactly one cohort — grouping is a partition, nothing is dropped.
    flat = [s for g in model.cohorts for s in g]
    assert sorted(flat) == sorted(series) and len(flat) == len(set(flat))


def test_model_symbols_are_aligned_with_corr_matrix():
    """model.symbols must index model.corr positionally (a misaligned lookup once displayed 0.14 for a
    pair whose true correlation was ~0.65)."""
    series = _sector_series()
    cands = dict(reversed(list({s: _candles(v) for s, v in series.items()}.items())))  # scrambled insertion order
    model = cohorts.build(cands)
    idx = {s: i for i, s in enumerate(model.symbols)}
    assert model.symbols == sorted(model.symbols)
    assert model.corr[idx["TCS.NS"], idx["INFY.NS"]] > 0.6
    assert model.corr[idx["TCS.NS"], idx["HDFCBANK.NS"]] < 0.4


def test_intra_cohort_correlation_exceeds_inter():
    series = _sector_series()
    model = cohorts.build({s: _candles(v) for s, v in series.items()})
    symbols, rets, _ = cohorts.returns_matrix({s: _candles(v) for s, v in series.items()})
    C = cohorts.corr_matrix(rets); idx = {s: i for i, s in enumerate(symbols)}
    intra = np.mean([C[idx[a], idx[b]] for g in model.cohorts if len(g) > 1 for a in g for b in g if a < b])
    inter = np.mean([C[idx[a], idx[b]] for ga in model.cohorts for gb in model.cohorts if ga < gb for a in ga for b in gb])
    assert intra > 0.6 > inter


def test_thin_history_is_forced_singleton():
    series = _sector_series()
    cands = {s: _candles(v) for s, v in series.items()}
    cands["NEWLISTING.NS"] = _candles(1000 * np.exp(np.cumsum(np.random.default_rng(3).normal(0, 0.01, 40))))
    model = cohorts.build(cands)
    assert [ "NEWLISTING.NS" ] in model.cohorts
    assert "NEWLISTING.NS" not in model.sigma_resid


def test_misaligned_calendars_are_aligned_on_common_days():
    series = _sector_series()
    cands = {s: _candles(v) for s, v in series.items()}
    # Give one symbol a different holiday: drop a day. Alignment must still work (common days), not crash.
    holiday = cands["TCS.NS"][10].day
    cands["TCS.NS"] = [c for c in cands["TCS.NS"] if c.day != holiday]
    symbols, rets, days = cohorts.returns_matrix(cands)
    assert holiday not in days and rets.shape[1] == len(symbols) and rets.shape[0] == len(days) - 1


def test_peer_residual_flags_the_one_moving_alone_not_the_pack():
    series = _sector_series()
    model = cohorts.build({s: _candles(v) for s, v in series.items()})
    # Whole IT pack down ~2%; INFY down 6% -> INFY moving alone; TCS/WIPRO are just the pack.
    moves = {"TCS.NS": -0.020, "INFY.NS": -0.060, "WIPRO.NS": -0.021, "HDFCBANK.NS": 0.001, "ICICIBANK.NS": 0.0,
             "SBIN.NS": -0.001, "LONER.NS": 0.03}
    res = cohorts.peer_residuals(model, moves, elapsed_seconds=3 * 22500, trading_day_s=22500, min_elapsed_s=900)
    assert res["LONER.NS"] is None                         # singleton -> caller falls back to beta residual
    assert abs(res["INFY.NS"][1]) >= cohorts.PEER_Z_FLAG   # moving alone
    assert abs(res["TCS.NS"][1]) < cohorts.PEER_Z_FLAG     # moving with the pack -> not promoted
    assert abs(res["WIPRO.NS"][1]) < cohorts.PEER_Z_FLAG
    # Regression: results must be PLAIN Python floats (numpy scalars break Pydantic/JSON serialization —
    # a numpy.bool_ from `abs(np.float64) >= 2` 500'd /state once).
    for v in res.values():
        if v is not None:
            assert type(v[0]) is float and type(v[1]) is float
