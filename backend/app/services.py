"""M1 watchlist + change-since-last-seen logic.

Kept as plain functions over asyncpg + the provider. The one integrity-critical piece here is the
monotonic watermark in mark_seen: the read-state only ever advances forward, so a stale or
concurrent "mark seen" (e.g. a second device) can never move it backward.
"""
from __future__ import annotations

import json
from datetime import datetime

from . import db
from .models import Change, ChangeRow, Snapshot, WatchRow
from .providers import Quote, get_provider
from .symbols import normalize

# Naive M1 threshold; replaced by the volatility-normalized scoring engine in M4.
_NAIVE_MEANINGFUL_PCT = 0.5


def compute_change(seen_price: float, current_price: float) -> Change:
    abs_delta = round(current_price - seen_price, 4)
    pct = round((current_price - seen_price) / seen_price * 100, 4) if seen_price else 0.0
    direction = "up" if abs_delta > 0 else "down" if abs_delta < 0 else "flat"
    return Change(abs=abs_delta, pct=pct, direction=direction)


async def add_symbol(user_id: str, raw_symbol: str) -> str:
    symbol = normalize(raw_symbol)
    async with db.pool().acquire() as conn:
        # Idempotent: adding a symbol already watched is a no-op, not an error.
        await conn.execute(
            "insert into watchlist_items (user_id, symbol) values ($1, $2) "
            "on conflict (user_id, symbol) do nothing",
            user_id, symbol,
        )
    return symbol


async def remove_symbol(user_id: str, symbol: str) -> None:
    symbol = normalize(symbol)
    async with db.pool().acquire() as conn:
        await conn.execute("delete from watchlist_items where user_id=$1 and symbol=$2", user_id, symbol)
        await conn.execute("delete from read_state where user_id=$1 and symbol=$2", user_id, symbol)


async def _symbols_for(user_id: str) -> list[str]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            "select symbol from watchlist_items where user_id=$1 order by created_at", user_id
        )
    return [r["symbol"] for r in rows]


async def _read_state(user_id: str) -> dict[str, dict]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            "select symbol, watermark_event_time, snapshot_json from read_state where user_id=$1", user_id
        )
    return {r["symbol"]: {"watermark": r["watermark_event_time"], "snapshot": json.loads(r["snapshot_json"])} for r in rows}


async def list_watchlist(user_id: str) -> list[WatchRow]:
    symbols = await _symbols_for(user_id)
    if not symbols:
        return []
    quotes = await get_provider().get_quotes(symbols)
    seen = await _read_state(user_id)

    out: list[WatchRow] = []
    for sym in symbols:
        q = quotes[sym]
        row = WatchRow(
            symbol=sym, price=q.price, event_time=q.event_time, source=q.source, has_baseline=False
        )
        if sym in seen:
            snap = seen[sym]["snapshot"]
            row.has_baseline = True
            row.last_seen = Snapshot(price=snap["price"], event_time=snap["event_time"])
            row.change_since_seen = compute_change(snap["price"], q.price)
        out.append(row)
    return out


async def mark_seen(user_id: str, symbol: str) -> None:
    """Freeze what the user is looking at now as their baseline, advancing the watermark forward only."""
    symbol = normalize(symbol)
    q: Quote = await get_provider().get_quote(symbol)
    snapshot = json.dumps({"price": q.price, "event_time": q.event_time.isoformat(), "source": q.source})
    async with db.pool().acquire() as conn:
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
            user_id, symbol, q.event_time, snapshot,
        )


async def mark_all_seen(user_id: str) -> None:
    for sym in await _symbols_for(user_id):
        await mark_seen(user_id, sym)


async def get_changes(user_id: str) -> list[ChangeRow]:
    """The 'while you were away' feed: symbols that moved since the user's snapshot, ranked by magnitude."""
    rows = [r for r in await list_watchlist(user_id) if r.has_baseline and r.change_since_seen]
    rows.sort(key=lambda r: abs(r.change_since_seen.pct), reverse=True)  # type: ignore[union-attr]

    out: list[ChangeRow] = []
    for r in rows:
        ch = r.change_since_seen
        assert ch is not None and r.last_seen is not None
        if ch.direction == "flat":
            continue
        meaningful = abs(ch.pct) >= _NAIVE_MEANINGFUL_PCT
        reason = f"{'+' if ch.abs >= 0 else ''}{ch.pct:.2f}% since you last looked"
        out.append(
            ChangeRow(
                symbol=r.symbol, price=r.price, event_time=r.event_time,
                last_seen=r.last_seen, change_since_seen=ch, is_meaningful=meaningful, reason=reason,
            )
        )
    return out
