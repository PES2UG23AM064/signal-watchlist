"""Quote persistence: ingestion sanity checks, latest-per-symbol reads and freshness.

Quotes are append-only and "latest" means max(event_time), so a delayed arrival can never become what
the user sees. A quote that fails a sanity check is stored with is_suspect=true for audit but never served.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg

from .providers import Quote

# Sanity thresholds.
MAX_TICK_JUMP = 0.20  # NSE's widest circuit band; a bigger single-tick jump is bad upstream data
# A jump is only judged against a recent reference: an hour-old quote may simply predate a real gap,
# and a wrong-but-latest reference would otherwise quarantine every correct quote forever.
JUMP_REF_MAX_AGE_S = 300
# With no recent reference, bound against the stock's real last close instead (NSE bands).
MAX_STALE_REF_BAND = 0.30
# A future-stamped quote would pin itself as the permanent latest and report "fresh" forever.
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
        return ((now or datetime.now(UTC)) - self.event_time).total_seconds()

    def freshness(self, now: datetime | None = None) -> str:
        age = self.age_seconds(now)
        if age <= FRESH_MAX_S:
            return "fresh"
        if age <= DELAYED_MAX_S:
            return "delayed"
        return "stale"


def is_suspect(quote: Quote, prev_price: float | None, now: datetime | None = None,
               prev_event_time: datetime | None = None, anchor: float | None = None) -> bool:
    """Suspect if structurally impossible, stamped in the future, an implausible jump vs a recent
    reference, or (when the only reference is stale) implausibly far from the last close (`anchor`)."""
    now = now or datetime.now(UTC)
    if quote.price <= 0 or quote.volume < 0:
        return True
    if (quote.event_time - now).total_seconds() > MAX_FUTURE_SKEW_S:
        return True
    if prev_price is not None and prev_price > 0:
        ref_is_recent = prev_event_time is None or (now - prev_event_time).total_seconds() <= JUMP_REF_MAX_AGE_S
        if ref_is_recent:
            return abs(quote.price - prev_price) / prev_price > MAX_TICK_JUMP
    if anchor is not None and anchor > 0:
        return abs(quote.price - anchor) / anchor > MAX_STALE_REF_BAND
    return False


async def record_quote(conn: asyncpg.Connection, quote: Quote, prev: LatestQuote | None,
                       role: str = "primary", anchor: float | None = None) -> bool:
    """Append a quote (idempotent on (symbol, event_time)). Returns True if it passed sanity.
    role='secondary' quotes are a cross-check only and are never served."""
    suspect = is_suspect(quote, prev.price if prev else None,
                         prev_event_time=prev.event_time if prev else None, anchor=anchor)
    await conn.execute(
        """
        insert into quotes (symbol, price, volume, event_time, source, is_suspect, role)
        values ($1, $2, $3, $4, $5, $6, $7)
        on conflict (symbol, event_time) do nothing
        """,
        quote.symbol, quote.price, quote.volume, quote.event_time, quote.source, suspect, role,
    )
    return not suspect


async def disputes(conn: asyncpg.Connection, symbols: list[str], threshold_pct: float, window_s: int) -> dict[str, dict]:
    """Symbols where a secondary feed's latest quote (within the window) diverges from the served primary
    beyond the threshold. The primary is still served; the disagreement is shown, not resolved."""
    if not symbols:
        return {}
    rows = await conn.fetch(
        """
        with p as (
          select distinct on (symbol) symbol, price, event_time, source from quotes
          where symbol = any($1::text[]) and role='primary' and not is_suspect order by symbol, event_time desc),
        s as (
          select distinct on (symbol) symbol, price, event_time, source from quotes
          where symbol = any($1::text[]) and role='secondary' and not is_suspect order by symbol, event_time desc)
        select p.symbol, p.price as primary_price, p.source as primary_source,
               s.price as secondary_price, s.source as secondary_source, s.event_time as secondary_time
        from p join s using (symbol)
        where s.event_time > now() - make_interval(secs => $2)
          and abs(s.price - p.price) / p.price * 100 > $3
        """,
        symbols, float(window_s), float(threshold_pct),
    )
    return {r["symbol"]: {"primary_price": r["primary_price"], "primary_source": r["primary_source"],
                          "secondary_price": r["secondary_price"], "secondary_source": r["secondary_source"],
                          "divergence_pct": round(abs(r["secondary_price"] - r["primary_price"]) / r["primary_price"] * 100, 2)}
            for r in rows}


async def latest_quotes(conn: asyncpg.Connection, symbols: list[str]) -> dict[str, LatestQuote]:
    """Latest non-suspect primary quote per symbol; suspect and secondary rows are never served."""
    if not symbols:
        return {}
    rows = await conn.fetch(
        """
        select distinct on (symbol) symbol, price, volume, event_time, source
        from quotes
        where symbol = any($1::text[]) and is_suspect = false and role = 'primary'
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
