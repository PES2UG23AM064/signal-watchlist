"""Quote sanity boundary — garbage is flagged suspect (and later quarantined), never crashes."""
from datetime import datetime, timedelta, timezone

from app.providers import Quote
from app.quotes import is_suspect


def _q(price, volume=1_000_000, event_time=None):
    return Quote(
        symbol="X.NS", price=price, volume=volume,
        event_time=event_time or datetime.now(timezone.utc), source="replay",
    )


def test_normal_quote_is_ok():
    assert is_suspect(_q(1300.0), prev_price=1298.0) is False


def test_zero_price_is_suspect():
    assert is_suspect(_q(0.0), prev_price=1300.0) is True


def test_negative_price_is_suspect():
    assert is_suspect(_q(-5.0), prev_price=1300.0) is True


def test_negative_volume_is_suspect():
    assert is_suspect(_q(1300.0, volume=-1), prev_price=1300.0) is True


def test_implausible_single_tick_jump_is_suspect():
    # +100% in one tick is not a real NSE move
    assert is_suspect(_q(2600.0), prev_price=1300.0) is True


def test_normal_move_within_band_is_ok():
    # a real ~3% move must NOT be flagged
    assert is_suspect(_q(1339.0), prev_price=1300.0) is False


def test_jump_guard_sits_at_nse_circuit_limit():
    # NSE circuit filters cap a single-session move at 20%: 15% is a (violent but real) move,
    # 25% in one tick is bad or conflicting data. This exact case bit us: two pollers with different
    # anchors alternated ~43% swings on one symbol.
    assert is_suspect(_q(1495.0), prev_price=1300.0) is False   # +15%
    assert is_suspect(_q(1625.0), prev_price=1300.0) is True    # +25%
    assert is_suspect(_q(1860.0), prev_price=1300.0) is True    # +43% (the real incident)


def test_no_prev_price_only_checks_structure():
    assert is_suspect(_q(1300.0), prev_price=None) is False
    assert is_suspect(_q(0.0), prev_price=None) is True


def test_jump_only_judged_against_recent_reference():
    # A 30% jump vs a quote from 5s ago is garbage. Vs a quote from an hour ago it may be a legitimate
    # gap — and a stale wrong reference must never quarantine correct data forever (the poison we hit).
    now = datetime.now(timezone.utc)
    q = _q(1690.0)  # +30% vs 1300
    assert is_suspect(q, prev_price=1300.0, now=now, prev_event_time=now - timedelta(seconds=5)) is True
    assert is_suspect(q, prev_price=1300.0, now=now, prev_event_time=now - timedelta(hours=1)) is False
    # Unknown reference age -> conservative: still judged.
    assert is_suspect(q, prev_price=1300.0, now=now, prev_event_time=None) is True


def test_future_timestamp_is_suspect():
    # A quote stamped far in the future would poison the latest-by-event_time read forever.
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert is_suspect(_q(1300.0, event_time=future), prev_price=1300.0) is True
    # A tiny clock skew within tolerance is fine.
    near = datetime.now(timezone.utc) + timedelta(seconds=2)
    assert is_suspect(_q(1300.0, event_time=near), prev_price=1300.0) is False
