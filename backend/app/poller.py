"""Shared per-symbol poller — the ingestion backbone.

Runs as an in-process asyncio task inside FastAPI's lifespan (single Render container; the free tier
has no separate worker type), but is written decoupled so it also runs standalone:  python -m app.poller

Key property — FAN-IN: it polls each UNIQUE symbol once per cycle, no matter how many users watch it
(RELIANCE watched by 500 users = one upstream fetch, not 500). That is the scalability story; a naive
per-user-per-symbol poll is what it deliberately avoids.

Single process => exactly one poller by construction, so no leader election is needed here. The
multi-instance path (advisory-lock election) is documented, not built.
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg

from . import db, quotes
from .config import settings
from .providers import get_provider

log = logging.getLogger("poller")


async def _unique_symbols(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch("select distinct symbol from watchlist_items")
    return [r["symbol"] for r in rows]


async def poll_once(pool: asyncpg.Pool) -> int:
    """One fan-in cycle: fetch every watched symbol once, append quotes. Returns #symbols polled."""
    provider = get_provider()
    async with pool.acquire() as conn:
        symbols = await _unique_symbols(conn)
        if not symbols:
            return 0
        prev = await quotes.latest_quotes(conn, symbols)  # for single-tick jump sanity checks

    fetched = await provider.get_quotes(symbols)  # one shared upstream call for all users

    async with pool.acquire() as conn:
        async with conn.transaction():
            for sym, q in fetched.items():
                prev_price = prev[sym].price if sym in prev else None
                await quotes.record_quote(conn, q, prev_price)
    return len(symbols)


async def prune(pool: asyncpg.Pool) -> int:
    """Bounded retention so the time-series never grows without limit."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "delete from quotes where event_time < now() - ($1 || ' hours')::interval",
            str(settings.quote_retention_hours),
        )
    # result like "DELETE 42"
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def run(stop: asyncio.Event) -> None:
    """Poll loop. Never lets one bad cycle kill the poller (resilience); prunes periodically."""
    interval = settings.poll_interval_seconds
    prune_every = max(1, int(settings.prune_interval_seconds / interval))
    cycle = 0
    log.info("poller started (interval=%ss, provider=%s)", interval, get_provider().name)
    while not stop.is_set():
        try:
            n = await poll_once(db.pool())
            cycle += 1
            if n and cycle % prune_every == 0:
                deleted = await prune(db.pool())
                if deleted:
                    log.info("pruned %s old quote rows", deleted)
        except Exception:  # noqa: BLE001 — a bad cycle must not stop ingestion
            log.exception("poll cycle failed; continuing")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    log.info("poller stopped")


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    await db.connect()
    await db.run_migrations()
    stop = asyncio.Event()
    try:
        await run(stop)
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(_main())
