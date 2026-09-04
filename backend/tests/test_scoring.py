"""Scoring engine invariants. The no-look-ahead test is written FIRST and is the most important one:
it forces the feature code to be trailing-by-construction, which is what makes the backtest honest."""
from __future__ import annotations

import math

import numpy as np

from app import scoring
from app.scoring import Features, features_for_bar, flags_and_reasons, is_meaningful, live_features, unusualness


def _series(n=400, seed=7):
    rng = np.random.default_rng(seed)
    closes = 1000.0 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    idx = 20000.0 * np.exp(np.cumsum(rng.normal(0, 0.007, n)))
    vols = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return closes, vols, idx


def test_no_look_ahead():
    """features(bars[:t+1])[t] must equal features(all bars)[t] — future bars cannot change the past."""
    closes, vols, idx = _series()
    for t in (scoring.MIN_BARS, 120, 250, 399):
        full = features_for_bar(closes, vols, idx, t)
        trunc = features_for_bar(closes[: t + 1], vols[: t + 1], idx[: t + 1], t)
        assert full is not None and trunc is not None
        assert np.allclose(full.vector(), trunc.vector()), f"look-ahead leak at t={t}"


def test_not_scorable_without_history():
    closes, vols, idx = _series()
    assert features_for_bar(closes, vols, idx, scoring.MIN_BARS - 1) is None


def test_cross_flag_reference_excludes_current_bar():
    closes, vols, idx = _series()
    t = 300
    # Force bar t to be a fresh high above ALL prior bars -> must flag "high".
    c = closes.copy(); c[t] = c[:t].max() * 1.02
    f = features_for_bar(c, vols, idx, t)
    assert f is not None and f.crossed == "high" and f.cross_flag == 1.0
    # A bar that merely EQUALS the prior trailing-window max is NOT a break. (The reference is the
    # trailing LOOKBACK window, not all-time history.)
    c2 = closes.copy(); c2[t] = c2[max(0, t - scoring.LOOKBACK + 1): t].max()
    f2 = features_for_bar(c2, vols, idx, t)
    assert f2 is not None and f2.crossed is None


def test_live_sqrt_time_scaling():
    """Same % move over a longer window is LESS surprising (sigma * sqrt(elapsed) grows)."""
    common = dict(price_now=1030.0, price_seen=1000.0, idx_now=None, idx_seen=None,
                  sigma_daily=0.012, beta=None, avg_vol20=2e6, vol_today=2e6, hi52=1500, lo52=800)
    short = live_features(elapsed_seconds=3600, **common)
    long_ = live_features(elapsed_seconds=3 * scoring.TRADING_DAY_S, **common)
    assert short is not None and long_ is not None
    assert short.abs_resid_z > long_.abs_resid_z
    # Elapsed is floored so a 10-second refresh doesn't explode the z-score.
    tiny = live_features(elapsed_seconds=10, **common)
    floor = live_features(elapsed_seconds=scoring.MIN_ELAPSED_S, **common)
    assert tiny is not None and floor is not None
    assert math.isclose(tiny.abs_resid_z, floor.abs_resid_z)


def test_live_market_adjustment_uses_beta():
    """A move that is fully explained by the index (beta * index_move) has ~zero residual."""
    f = live_features(price_now=1020.0, price_seen=1000.0, idx_now=20400.0, idx_seen=20000.0,
                      elapsed_seconds=3600, sigma_daily=0.012, beta=1.0, avg_vol20=2e6, vol_today=2e6,
                      hi52=1500, lo52=800)
    assert f is not None
    assert abs(f.resid_pct) < 1e-9          # 2% stock move, 2% index move, beta 1 -> nothing idiosyncratic
    assert f.abs_resid_z < 0.01


def test_flags_are_plain_english_and_threshold_driven():
    """Reasons are short plain-English phrases (the numbers live in the explain panel); they come from
    fixed thresholds only — no model gates them (rerank-never-suppress)."""
    big = Features(abs_resid_z=2.6, log_vol_ratio=math.log(3.1), cross_flag=1.0, resid_pct=0.03,
                   move_pct=0.03, vol_ratio=3.1, crossed="high", sigma_used=0.012)
    reasons = flags_and_reasons(big)
    assert len(reasons) == 3
    assert reasons[0].startswith("Unusual move") and "σ" not in reasons[0]   # sigma stays in the panel
    assert reasons[1].startswith("Volume") and "3.1" in reasons[1]
    assert reasons[2] == "Broke its 52-week high"
    quiet = Features(abs_resid_z=0.3, log_vol_ratio=0.0, cross_flag=0.0, resid_pct=0.002,
                     move_pct=0.002, vol_ratio=1.0, crossed=None, sigma_used=0.012)
    assert flags_and_reasons(quiet) == []
    # Unusualness is the ranking score (descriptive); it never decides visibility.
    assert unusualness(big) > unusualness(quiet)


def test_volume_alone_never_promotes():
    """Volume vs the 20-day average is about today, not about the user's window: it is a reason (chip) and
    it ranks, but a symbol that moved 0.0% since you looked must not 'need attention' for it."""
    busy_flat = Features(abs_resid_z=0.1, log_vol_ratio=math.log(2.4), cross_flag=0.0, resid_pct=0.0005,
                         move_pct=0.0005, vol_ratio=2.4, crossed=None, sigma_used=0.012)
    assert flags_and_reasons(busy_flat) == ["Volume 2.4× normal"]
    assert not is_meaningful(busy_flat)
    moved = Features(abs_resid_z=2.3, log_vol_ratio=0.0, cross_flag=0.0, resid_pct=0.02,
                     move_pct=0.02, vol_ratio=1.0, crossed=None, sigma_used=0.012)
    assert is_meaningful(moved)
    broke = Features(abs_resid_z=0.5, log_vol_ratio=0.0, cross_flag=1.0, resid_pct=0.004,
                     move_pct=0.004, vol_ratio=1.0, crossed="low", sigma_used=0.012)
    assert is_meaningful(broke)


def test_priority_monotonic_in_move_size():
    """Ranking is descriptive: more market-adjusted movement -> strictly higher unusualness."""
    def mk(z):
        return Features(abs_resid_z=z, log_vol_ratio=0.0, cross_flag=0.0, resid_pct=0.0,
                        move_pct=0.0, vol_ratio=1.0, crossed=None, sigma_used=0.01)
    us = [unusualness(mk(z)) for z in (0.5, 1.0, 2.0, 3.0)]
    assert us == sorted(us) and len(set(us)) == 4, f"unusualness not strictly monotonic: {us}"
