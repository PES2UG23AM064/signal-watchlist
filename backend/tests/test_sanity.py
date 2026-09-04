"""Quote sanity boundary: garbage is flagged suspect (then quarantined), never raises."""
from datetime import UTC, datetime, timedelta

from app.providers import Quote
from app.quotes import is_suspect


def _q(price, volume=1_000_000, event_time=None):
    return Quote(
        symbol="X.NS", price=price, volume=volume,
        event_time=event_time or datetime.now(UTC), source="replay",
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
    # +100% in one tick cannot be a real NSE move
    assert is_suspect(_q(2600.0), prev_price=1300.0) is True


def test_normal_move_within_band_is_ok():
    # a real ~3% move must not be flagged
    assert is_suspect(_q(1339.0), prev_price=1300.0) is False


def test_jump_guard_sits_at_nse_circuit_limit():
    # NSE circuit filters cap a single-session move at 20%: 15% is violent but real, 25% in one tick
    # is bad or conflicting data (e.g. two pollers with different anchors alternating on one symbol).
    assert is_suspect(_q(1495.0), prev_price=1300.0) is False   # +15%
    assert is_suspect(_q(1625.0), prev_price=1300.0) is True    # +25%
    assert is_suspect(_q(1860.0), prev_price=1300.0) is True    # +43%


def test_no_prev_price_only_checks_structure():
    assert is_suspect(_q(1300.0), prev_price=None) is False
    assert is_suspect(_q(0.0), prev_price=None) is True


def test_jump_only_judged_against_recent_reference():
    # A 30% jump vs a 5s-old quote is garbage; vs an hour-old quote it may be a legitimate gap.
    # A stale wrong reference must never quarantine correct data forever.
    now = datetime.now(UTC)
    q = _q(1690.0)  # +30% vs 1300
    assert is_suspect(q, prev_price=1300.0, now=now, prev_event_time=now - timedelta(seconds=5)) is True
    assert is_suspect(q, prev_price=1300.0, now=now, prev_event_time=now - timedelta(hours=1)) is False
    # Unknown reference age is treated conservatively: still judged.
    assert is_suspect(q, prev_price=1300.0, now=now, prev_event_time=None) is True


def test_stale_reference_escape_hatch_is_bounded_by_the_anchor():
    # With only a stale reference the quote is still judged against the last close (anchor):
    # +35% vs anchor is beyond the 30% band -> garbage; +15% is a legitimate gap.
    now = datetime.now(UTC)
    stale = now - timedelta(hours=1)
    assert is_suspect(_q(1750.0), prev_price=1300.0, now=now, prev_event_time=stale, anchor=1300.0) is True
    assert is_suspect(_q(1495.0), prev_price=1300.0, now=now, prev_event_time=stale, anchor=1300.0) is False
    # First quote ever for a symbol: the anchor alone bounds it.
    assert is_suspect(_q(1750.0), prev_price=None, anchor=1300.0) is True
    assert is_suspect(_q(1350.0), prev_price=None, anchor=1300.0) is False
    # A recent reference is authoritative; the anchor is not consulted, so an intraday drift away
    # from the close is fine as long as each tick is plausible vs the last.
    assert is_suspect(_q(1690.0), prev_price=1650.0, now=now, prev_event_time=now - timedelta(seconds=5),
                      anchor=1300.0) is False


def test_future_timestamp_is_suspect():
    # A future-stamped quote would win every latest-by-event_time read until the clock caught up.
    future = datetime.now(UTC) + timedelta(minutes=1)
    assert is_suspect(_q(1300.0, event_time=future), prev_price=1300.0) is True
    # Small clock skew within tolerance is fine.
    near = datetime.now(UTC) + timedelta(seconds=2)
    assert is_suspect(_q(1300.0, event_time=near), prev_price=1300.0) is False
