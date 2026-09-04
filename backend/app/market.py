"""NSE equity market status in IST: 09:15-15:30, Mon-Fri, excluding trading holidays.

"Market closed" is a separate axis from "data is stale": the exchange can be closed while
simulated data is fresh, and data can be stale while the exchange is open.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OPEN_TIME = time(9, 15)
CLOSE_TIME = time(15, 30)

# NSE equity trading holidays 2026 (source: https://groww.in/p/nse-holidays, fetched 2026-09-04).
# Sources differ on a few election/Muhurat days; reconcile against NSE circulars if disputed.
NSE_HOLIDAYS_2026: dict[str, str] = {
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}


@dataclass(frozen=True)
class MarketStatus:
    is_open: bool
    label: str      # short pill text, e.g. "Market open" / "Market closed"
    detail: str     # why, e.g. "Holiday: Republic Day" / "Weekend" / "Opens 9:15 AM IST"


# Years with a holiday table. Outside these, session hours are reported as unverified, not open.
KNOWN_HOLIDAY_YEARS = {2026}


def _holiday_name(d: date) -> str | None:
    return NSE_HOLIDAYS_2026.get(d.isoformat())


def market_status(now_utc: datetime | None = None) -> MarketStatus:
    now = (now_utc or datetime.now(UTC)).astimezone(IST)
    d, t = now.date(), now.time()

    holiday = _holiday_name(d)
    if holiday is not None:
        return MarketStatus(False, "Market closed", f"Holiday: {holiday}")
    if now.weekday() >= 5:
        return MarketStatus(False, "Market closed", "Weekend")
    if t < OPEN_TIME:
        return MarketStatus(False, "Market closed", "Opens 9:15 AM IST")
    if t >= CLOSE_TIME:
        return MarketStatus(False, "Market closed", "Closed 3:30 PM IST")
    if d.year not in KNOWN_HOLIDAY_YEARS:
        return MarketStatus(True, "Market open?", "Session hours (holiday calendar unverified for this year)")
    return MarketStatus(True, "Market open", "NSE 9:15 AM - 3:30 PM IST")
