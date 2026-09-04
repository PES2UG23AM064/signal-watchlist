"""Replay simulator: deterministic, anchored to real closes, never invents a price."""
from __future__ import annotations

import asyncio

import pytest

from app.providers.replay import NoAnchor, ReplayProvider


def test_same_seed_same_time_same_price():
    a, b = ReplayProvider(seed=42), ReplayProvider(seed=42)
    assert a.price_volume_at("RELIANCE.NS", 1_800_000_000.0) == b.price_volume_at("RELIANCE.NS", 1_800_000_000.0)


def test_default_demo_names_quote_before_baselines_load():
    p = ReplayProvider()
    q = asyncio.run(p.get_quote("TCS.NS"))
    assert q.source == "replay" and 2000 < q.price < 2600   # around the built-in real anchor


def test_real_baseline_anchor_overrides_builtin():
    p = ReplayProvider()
    p.set_profile("TCS.NS", anchor=3000.0, sigma_daily=0.02, avg_vol=1_000_000)
    assert 2900 < p.price_volume_at("TCS.NS", 1_800_000_000.0)[0] < 3100


def test_unknown_symbol_is_not_given_an_invented_price():
    p = ReplayProvider()
    assert not p.can_quote("DELISTED.NS")
    # Built-in demo anchors let default names quote but are not loaded profiles; the poller uses
    # has_profile() to know when a symbol's real baseline still needs syncing into the simulator.
    assert p.can_quote("TCS.NS") and not p.has_profile("TCS.NS")
    p.set_profile("TCS.NS", anchor=2300.0, sigma_daily=0.0176, avg_vol=2_500_000)
    assert p.has_profile("TCS.NS")
    with pytest.raises(NoAnchor):
        asyncio.run(p.get_quote("DELISTED.NS"))
    # a batch omits the unknown symbol instead of failing the whole call
    out = asyncio.run(p.get_quotes(["TCS.NS", "DELISTED.NS", "INFY.NS"]))
    assert set(out) == {"TCS.NS", "INFY.NS"}


def test_pack_members_share_one_move_and_exactly_one_diverges():
    """Non-designated pack members move only with the pack (same % move, so they fold into a group card);
    exactly one designated member keeps its own event on top."""
    p = ReplayProvider(seed=7)
    for s, sig in (("A.NS", 0.015), ("B.NS", 0.02), ("C.NS", 0.018)):
        p.set_profile(s, anchor=1000.0, sigma_daily=sig, avg_vol=1_000_000)
    p.set_groups([["A.NS", "B.NS", "C.NS"]])
    assert sum(p.designated(s) for s in ("A.NS", "B.NS", "C.NS")) == 1
    # Find a moment just after a pack event fired (volume burst); packs fire every other period, so scan two.
    t = next(t for t in (1_800_000_000.0 + s for s in range(0, 2 * 20 * 60, 10)) if p.pack_offset_at("A.NS", t)[1] >= 1.8)
    # A sector day is not a permanent state: over four periods, some have no pack event.
    periods = range(int(t // (20 * 60)), int(t // (20 * 60)) + 4)
    firing = [p.pack_fires("A.NS", per) for per in periods]
    assert any(firing) and not all(firing), "pack fires every period — the digest would flag every member all the time"
    off_a, off_b, off_c = (p.pack_offset_at(s, t)[0] for s in ("A.NS", "B.NS", "C.NS"))
    assert off_a == off_b == off_c                                  # one shared % move
    # The step the event just made is >= 2 sigma_eff of the median sigma, so every member flags.
    # The absolute level may be smaller: sector days alternate direction, so a previous one may offset it.
    step = off_a - p.pack_offset_at("A.NS", t - 10)[0]
    assert abs(step) >= 2.0 * 0.2 * 0.018
    followers = [s for s in ("A.NS", "B.NS", "C.NS") if not p.designated(s)]
    # Followers' returns equal the pack move up to the small quiet drift.
    for s in followers:
        ret = p.price_volume_at(s, t)[0] / 1000.0 - 1.0
        assert abs(ret - off_a) < 0.003
    # Without packs the same symbols are independent.
    q = ReplayProvider(seed=7)
    q.set_profile("A.NS", anchor=1000.0, sigma_daily=0.015, avg_vol=1_000_000)
    assert q.pack_offset_at("A.NS", t) == (0.0, 1.0) and q.designated("A.NS")


def test_calm_windows_never_flag():
    """A scripted event is a permanent step, never a sawtooth: between events (including across a period
    boundary) no 15-minute window may move more than the 2-sigma_eff flag."""
    p = ReplayProvider(seed=42)
    p.set_profile("HDFCBANK.NS", anchor=1000.0, sigma_daily=0.01, avg_vol=1_000_000)   # role: sigma_up
    sigma_eff = 0.01 * 0.2
    t0 = 1_800_000_000.0
    quotes = {s: p.price_volume_at("HDFCBANK.NS", t0 + s) for s in range(-10, 3 * 20 * 60, 10)}
    prices = {s: q[0] for s, q in quotes.items() if s >= 0}
    # Event moments are where the volume burst starts (>1.5x average; quiet noise stays within 0.9-1.1x).
    busy = {s: q[1] > 1.5 * 1_000_000 for s, q in quotes.items()}
    burst_starts = [s for s in prices if busy[s] and not busy[s - 10]]
    assert burst_starts, "no event fired in an hour"
    for s in prices:
        end = s + 15 * 60
        if end not in prices:
            continue
        if any(s < b <= end for b in burst_starts):
            continue   # window contains the event itself, which should flag
        move = abs(prices[end] - prices[s]) / 1000.0
        assert move < 2.0 * sigma_eff, f"a calm 15-minute window starting at +{s}s moved {move / sigma_eff:.1f} sigma_eff"


def test_quiet_role_stays_well_under_a_sigma_between_events():
    """TCS is scripted 'quiet': over a 15-minute window its drift stays well below the 2-sigma flag."""
    p = ReplayProvider()
    p.set_profile("TCS.NS", anchor=2300.0, sigma_daily=0.0176, avg_vol=2_500_000)
    t0 = 1_800_000_000.0
    prices = [p.price_volume_at("TCS.NS", t0 + s)[0] for s in range(0, 15 * 60, 30)]
    swing = (max(prices) - min(prices)) / 2300.0
    sigma_eff_15m = 0.0176 * 0.2
    assert swing < 1.0 * sigma_eff_15m
