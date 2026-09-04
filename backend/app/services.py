"""Watchlist + change-since-last-seen logic.

Reads serve quotes PERSISTED by the shared poller (not a live per-request fetch) — that's the fan-in:
one upstream poll per symbol, many readers. Two things worth calling out:
  * mark_seen's monotonic watermark: the read-state snapshot only ever advances forward, so a stale or
    concurrent "mark seen" (e.g. a second device) can never move a user's baseline backward.
  * We never hold a pooled DB connection across an upstream network fetch (that would exhaust the pool
    and stall the API); the bootstrap fetch in _ensure_quote happens OUTSIDE any acquired connection.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import asyncpg

from . import baselines, db, quotes
from .models import Change, ChangeRow, Provenance, Snapshot, WatchRow
from .providers import Quote, get_provider
from .quotes import LatestQuote
from .symbols import normalize

log = logging.getLogger("services")

# Naive M1 threshold; replaced by the volatility-normalized scoring engine in the next milestone.
_NAIVE_MEANINGFUL_PCT = 0.5


def compute_change(seen_price: float, current_price: float) -> Change:
    abs_delta = round(current_price - seen_price, 4)
    pct = round((current_price - seen_price) / seen_price * 100, 4) if seen_price else 0.0
    direction = "up" if abs_delta > 0 else "down" if abs_delta < 0 else "flat"
    return Change(abs=abs_delta, pct=pct, direction=direction)


def _provenance(lq: LatestQuote | None, now: datetime) -> Provenance:
    if lq is None:
        return Provenance(source="none", is_simulated=False, event_time=now, age_seconds=0.0, freshness="no_data")
    return Provenance(
        source=lq.source,
        is_simulated=lq.source == "replay",
        event_time=lq.event_time,
        age_seconds=round(lq.age_seconds(now), 1),
        freshness=lq.freshness(now),
    )


async def _ensure_quote(symbol: str) -> None:
    """Guarantee a symbol has a FRESH quote after add/seen without waiting a poll cycle — but reuse an
    existing fresh quote if one is already stored (fan-in: concurrent adders of the same symbol don't
    each hit upstream), and NEVER fetch while holding a pooled connection."""
    now = datetime.now(timezone.utc)
    async with db.pool().acquire() as conn:
        lq = (await quotes.latest_quotes(conn, [symbol])).get(symbol)
    if lq is not None and lq.freshness(now) == "fresh":
        return
    try:
        q: Quote = await get_provider().get_quote(symbol)  # outside any held connection
    except Exception:  # noqa: BLE001 — best-effort bootstrap; the poller will fill in within a cycle
        log.warning("bootstrap fetch failed for %s; poller will populate", symbol)
        return
    async with db.pool().acquire() as conn:
        prev = await quotes.latest_quotes(conn, [symbol])
        await quotes.record_quote(conn, q, prev[symbol].price if symbol in prev else None)


async def add_symbol(user_id: str, raw_symbol: str) -> str:
    symbol = normalize(raw_symbol)
    async with db.pool().acquire() as conn:
        await conn.execute(
            "insert into watchlist_items (user_id, symbol) values ($1, $2) "
            "on conflict (user_id, symbol) do nothing",
            user_id, symbol,
        )
    await _ensure_quote(symbol)  # bootstrap data (dedup'd, no network under a held connection)
    # Real daily-candle baselines (cached, refreshed when stale) so the scoring denominators are honest
    # from the moment a symbol is added. Yahoo down -> returns None and scoring degrades gracefully.
    await baselines.ensure_baselines(symbol)
    return symbol


async def remove_symbol(user_id: str, symbol: str) -> None:
    symbol = normalize(symbol)
    async with db.pool().acquire() as conn:
        await conn.execute("delete from watchlist_items where user_id=$1 and symbol=$2", user_id, symbol)
        await conn.execute("delete from read_state where user_id=$1 and symbol=$2", user_id, symbol)


async def _symbols_for(conn: asyncpg.Connection, user_id: str) -> list[str]:
    rows = await conn.fetch(
        "select symbol from watchlist_items where user_id=$1 order by created_at", user_id
    )
    return [r["symbol"] for r in rows]


async def _read_state(conn: asyncpg.Connection, user_id: str) -> dict[str, dict]:
    rows = await conn.fetch("select symbol, snapshot_json from read_state where user_id=$1", user_id)
    return {r["symbol"]: json.loads(r["snapshot_json"]) for r in rows}


async def list_watchlist(user_id: str) -> list[WatchRow]:
    now = datetime.now(timezone.utc)
    async with db.pool().acquire() as conn:
        symbols = await _symbols_for(conn, user_id)
        if not symbols:
            return []
        latest = await quotes.latest_quotes(conn, symbols)
        seen = await _read_state(conn, user_id)

    out: list[WatchRow] = []
    for sym in symbols:
        lq = latest.get(sym)
        row = WatchRow(
            symbol=sym,
            price=lq.price if lq else None,
            provenance=_provenance(lq, now),
            has_baseline=False,
        )
        if sym in seen and lq is not None:
            snap = seen[sym]
            row.has_baseline = True
            row.last_seen = Snapshot(price=snap["price"], event_time=snap["event_time"])
            row.change_since_seen = compute_change(snap["price"], lq.price)
        out.append(row)
    return out


async def _snapshot_upsert(conn: asyncpg.Connection, user_id: str, symbol: str, lq: LatestQuote) -> None:
    snapshot = json.dumps({"price": lq.price, "event_time": lq.event_time.isoformat(), "source": lq.source})
    await conn.execute(
        """
        insert into read_state (user_id, symbol, watermark_event_time, snapshot_json, seen_at)
        values ($1, $2, $3, $4::jsonb, now())
        on conflict (user_id, symbol) do update
          set watermark_event_time = excluded.watermark_event_time,
              snapshot_json        = excluded.snapshot_json,
              seen_at              = now()
          where excluded.watermark_event_time >= read_state.watermark_event_time
        """,
        user_id, symbol, lq.event_time, snapshot,
    )


async def mark_seen(user_id: str, symbol: str) -> None:
    """Freeze the quote the user is currently seeing as their baseline (monotonic watermark)."""
    symbol = normalize(symbol)
    await _ensure_quote(symbol)
    async with db.pool().acquire() as conn:
        latest = await quotes.latest_quotes(conn, [symbol])
        if symbol in latest:
            await _snapshot_upsert(conn, user_id, symbol, latest[symbol])


async def mark_all_seen(user_id: str) -> None:
    async with db.pool().acquire() as conn:
        async with conn.transaction():  # all-or-nothing: no partial mark-all on a mid-loop failure
            symbols = await _symbols_for(conn, user_id)
            latest = await quotes.latest_quotes(conn, symbols)
            for sym in symbols:
                if sym in latest:
                    await _snapshot_upsert(conn, user_id, sym, latest[sym])


def _changes_from_rows(rows: list[WatchRow]) -> list[ChangeRow]:
    """Derive the ranked 'while you were away' feed from already-computed watchlist rows (no re-query)."""
    candidates = [r for r in rows if r.has_baseline and r.change_since_seen and r.price is not None]
    candidates.sort(key=lambda r: abs(r.change_since_seen.pct), reverse=True)  # type: ignore[union-attr]

    out: list[ChangeRow] = []
    for r in candidates:
        ch = r.change_since_seen
        assert ch is not None and r.last_seen is not None and r.price is not None
        if ch.direction == "flat":
            continue
        meaningful = abs(ch.pct) >= _NAIVE_MEANINGFUL_PCT
        reason = f"{'+' if ch.abs >= 0 else ''}{ch.pct:.2f}% since you last looked"
        out.append(
            ChangeRow(
                symbol=r.symbol, price=r.price, provenance=r.provenance,
                last_seen=r.last_seen, change_since_seen=ch, is_meaningful=meaningful, reason=reason,
            )
        )
    return out


async def get_state(user_id: str) -> tuple[list[WatchRow], list[ChangeRow]]:
    """Watchlist + ranked changes in ONE pass — the endpoint the client polls (no duplicate work)."""
    rows = await list_watchlist(user_id)
    return rows, _changes_from_rows(rows)


async def get_changes(user_id: str) -> list[ChangeRow]:
    """'While you were away' feed (standalone). The client uses get_state to avoid re-querying."""
    return _changes_from_rows(await list_watchlist(user_id))
