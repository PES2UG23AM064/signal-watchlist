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


def test_no_prev_price_only_checks_structure():
    assert is_suspect(_q(1300.0), prev_price=None) is False
    assert is_suspect(_q(0.0), prev_price=None) is True


def test_future_timestamp_is_suspect():
    # A quote stamped far in the future would poison the latest-by-event_time read forever.
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert is_suspect(_q(1300.0, event_time=future), prev_price=1300.0) is True
    # A tiny clock skew within tolerance is fine.
    near = datetime.now(timezone.utc) + timedelta(seconds=2)
    assert is_suspect(_q(1300.0, event_time=near), prev_price=1300.0) is False
