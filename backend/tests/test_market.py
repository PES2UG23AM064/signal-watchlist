"""Market-hours logic (IST, NSE holidays). Pure + fast — no DB, no network."""
from datetime import datetime, timezone

from app.market import market_status

# All inputs are UTC; +5:30 gives IST. 2026-09-04 is a Friday.
def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_open_during_session():
    # 06:00 UTC = 11:30 IST Friday -> open
    assert market_status(_utc(2026, 9, 4, 6, 0)).is_open is True


def test_closed_before_open():
    # 03:00 UTC = 08:30 IST -> pre-open
    s = market_status(_utc(2026, 9, 4, 3, 0))
    assert s.is_open is False and "Opens" in s.detail


def test_closed_after_close():
    # 11:00 UTC = 16:30 IST -> post-close
    s = market_status(_utc(2026, 9, 4, 11, 0))
    assert s.is_open is False and "Closed" in s.detail


def test_weekend_closed():
    # 2026-09-05 is Saturday
    s = market_status(_utc(2026, 9, 5, 6, 0))
    assert s.is_open is False and s.detail == "Weekend"


def test_holiday_closed_even_in_session_hours():
    # 2026-01-26 Republic Day, 06:00 UTC = 11:30 IST (would otherwise be open)
    s = market_status(_utc(2026, 1, 26, 6, 0))
    assert s.is_open is False and "Republic Day" in s.detail
