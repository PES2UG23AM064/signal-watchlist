"""Digest ranking invariants (pure, no DB): rerank never suppresses, more unusual ranks higher,
holdings amplify but never drown an unusual move, attention score is monotonic in rupees at stake."""
from __future__ import annotations

from datetime import UTC, datetime

from app.models import Change, Explain, Provenance, Signal, Snapshot, WatchRow
from app.services import _changes_from_rows, attention_score


def _row(sym, pct, unusual, qty=None, price=1000.0, flat=False):
    now = datetime.now(UTC)
    seen_price = price / (1 + pct / 100)
    ch = Change(abs=round(price - seen_price, 4), pct=0.0 if flat else pct,
                direction="flat" if flat else ("up" if pct > 0 else "down"))
    sig = Signal(reasons=["x"] if unusual >= 2 else [], is_meaningful=unusual >= 2, unusualness=unusual,
                 explain=Explain(sigma_move=unusual, move_pct=pct, market_adjusted_pct=pct, vol_ratio=1.0,
                                 crossed=None, sigma_daily_pct=1.5, beta=0.9, elapsed_seconds=900))
    return WatchRow(symbol=sym, price=price, has_baseline=True, last_seen=Snapshot(price=seen_price, event_time=now),
                    change_since_seen=ch, signal=sig, quantity=qty,
                    impact_inr=(qty * (price - seen_price)) if qty else None,
                    provenance=Provenance(source="replay", is_simulated=True, event_time=now, age_seconds=1, freshness="fresh"))


def test_every_moved_symbol_appears_once_and_flat_is_excluded():
    rows = [_row("A.NS", 1.0, 0.5), _row("B.NS", -3.0, 2.5), _row("C.NS", 0.0, 0.0, flat=True), _row("D.NS", 0.2, 0.1)]
    out = _changes_from_rows(rows)
    syms = [c.symbol for c in out]
    assert sorted(syms) == ["A.NS", "B.NS", "D.NS"] and len(syms) == len(set(syms))
    # quiet symbols are still listed, just not flagged
    assert [c.signal.is_meaningful for c in out].count(True) == 1


def test_more_unusual_ranks_higher_without_holdings():
    out = _changes_from_rows([_row("A.NS", 1.0, 0.8), _row("B.NS", 2.0, 3.1), _row("C.NS", 5.0, 1.2)])
    assert [c.symbol for c in out] == ["B.NS", "C.NS", "A.NS"]


def test_holdings_amplify_but_do_not_drown_an_unusual_move():
    # Equal unusualness: the held one ranks first.
    out = _changes_from_rows([_row("HELD.NS", 1.0, 1.0, qty=500), _row("FREE.NS", 1.0, 1.0)])
    assert out[0].symbol == "HELD.NS" and out[0].impact_inr is not None
    # An unusual move on an unheld symbol still beats a tiny held wiggle.
    out = _changes_from_rows([_row("HELD.NS", 0.1, 0.2, qty=50), _row("BIG.NS", 4.0, 4.0)])
    assert out[0].symbol == "BIG.NS"


def test_attention_score_is_transparent_and_monotonic():
    import math
    assert attention_score(2.0, None) == 2.0
    assert attention_score(2.0, 0.0) == 2.0
    a, b, c = attention_score(2.0, 1_000), attention_score(2.0, 10_000), attention_score(2.0, 100_000)
    assert 2.0 < a < b < c
    assert math.isclose(b, 2.0 * (1 + math.log10(11)))   # the documented formula (~x2 at Rs10k)
    assert attention_score(2.0, -10_000) == b            # magnitude matters, sign does not
