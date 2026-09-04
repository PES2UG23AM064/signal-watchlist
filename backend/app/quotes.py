"""Quote persistence: the ingestion sanity boundary + latest-per-symbol reads + freshness.

Integrity model:
  * Every quote is appended (append-only time-series). "Latest = max(event_time)", so an out-of-order
    or delayed arrival can never become what the user sees — that is the event-time last-write-wins
    guarantee, achieved structurally (no separate guard needed).
  * A quote that fails a sanity check is stored with is_suspect=true (kept for audit) but NEVER served
    as truth: reads select the latest NON-suspect row. Garbage is quarantined, not crashed on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg

from .providers import Quote

# Sanity thresholds. NSE circuit filters cap a stock's single-session move at 20%, so a single-tick
# jump beyond that is not a market move — it's bad or CONFLICTING upstream data (we hit this for real:
# two pollers with different anchors writing to one table). Quarantine it rather than serve it.
MAX_TICK_JUMP = 0.20  # 20% — NSE's widest circuit band
# A jump is only judged against a RECENT reference. Against a quote from 5 seconds ago, a 30% jump is
# garbage; against one from an hour ago the market may simply have moved (overnight gap, an outage).
# Without this, a wrong-but-latest reference would quarantine every CORRECT quote forever — a poison
# we hit for real after the dual-writer incident.
JUMP_REF_MAX_AGE_S = 300
# A quote whose event_time is meaningfully in the FUTURE (provider clock skew, a bad
# regularMarketTime) is dangerous: since "latest = max(event_time)" and freshness = now - event_time,
# it would pin itself as the permanent latest and report negative age -> "fresh" forever. Quarantine it.
MAX_FUTURE_SKEW_S = 5

# Freshness thresholds (seconds). Tuned relative to the poll interval.
FRESH_MAX_S = 20
DELAYED_MAX_S = 120


@dataclass(frozen=True)
class LatestQuote:
    symbol: str
    price: float
    volume: int
    event_time: datetime
    source: str

    def age_seconds(self, now: datetime | None = None) -> float:
        return ((now or datetime.now(timezone.utc)) - self.event_time).total_seconds()

    def freshness(self, now: datetime | None = None) -> str:
        age = self.age_seconds(now)
        if age <= FRESH_MAX_S:
            return "fresh"
        if age <= DELAYED_MAX_S:
            return "delayed"
        return "stale"


def is_suspect(quote: Quote, prev_price: float | None, now: datetime | None = None,
               prev_event_time: datetime | None = None) -> bool:
    """A quote is suspect if it's structurally impossible, stamped in the future (which would poison
    the latest-by-event_time read), or an implausible single-tick jump vs a RECENT reference."""
    now = now or datetime.now(timezone.utc)
    if quote.price <= 0 or quote.volume < 0:
        return True
    if (quote.event_time - now).total_seconds() > MAX_FUTURE_SKEW_S:
        return True
    if prev_price is not None and prev_price > 0:
        ref_is_recent = prev_event_time is None or (now - prev_event_time).total_seconds() <= JUMP_REF_MAX_AGE_S
        if ref_is_recent and abs(quote.price - prev_price) / prev_price > MAX_TICK_JUMP:
            return True
    return False


async def record_quote(conn: asyncpg.Connection, quote: Quote, prev: "LatestQuote | None") -> bool:
    """Append a quote (idempotent on (symbol, event_time)). Returns True if it passed sanity."""
    suspect = is_suspect(quote, prev.price if prev else None, prev_event_time=prev.event_time if prev else None)
    await conn.execute(
        """
        insert into quotes (symbol, price, volume, event_time, source, is_suspect)
        values ($1, $2, $3, $4, $5, $6)
        on conflict (symbol, event_time) do nothing
        """,
        quote.symbol, quote.price, quote.volume, quote.event_time, quote.source, suspect,
    )
    return not suspect


async def latest_quotes(conn: asyncpg.Connection, symbols: list[str]) -> dict[str, LatestQuote]:
    """Latest NON-suspect quote per symbol (suspect rows are quarantined, never served as truth)."""
    if not symbols:
        return {}
    rows = await conn.fetch(
        """
        select distinct on (symbol) symbol, price, volume, event_time, source
        from quotes
        where symbol = any($1::text[]) and is_suspect = false
        order by symbol, event_time desc
        """,
        symbols,
    )
    return {
        r["symbol"]: LatestQuote(
            symbol=r["symbol"], price=r["price"], volume=r["volume"],
            event_time=r["event_time"], source=r["source"],
        )
        for r in rows
    }
