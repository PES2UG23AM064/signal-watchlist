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
from .baselines import INDEX_SYMBOL
from .config import settings
from .providers import get_provider

log = logging.getLogger("poller")

# Observability: what the poller is doing right now (served by GET /status).
STATS: dict = {"last_poll_at": None, "last_cycle_seconds": None, "last_symbols": 0, "cycles": 0, "errors": 0,
               "role": "starting", "leader_since": None}

# Leader election: exactly ONE poller writes, even if several instances run. A Postgres session-level
# advisory lock on a dedicated connection is the whole mechanism — it is released automatically if the
# leader's connection dies, so a follower takes over within one interval. This is what makes the
# dual-writer incident structurally impossible rather than a matter of discipline.
# NOTE: session-level locks need a SESSION-mode connection (Supabase pooler on 5432, not the transaction
# pooler on 6543, which recycles the physical session between transactions).
LEADER_LOCK_KEY = 0x5157_4C53  # arbitrary constant: "signal-watchlist poller"

# Dev/demo: symbols whose upstream is "down" until the given unix time. The poller skips them, so their
# latest quote AGES and the freshness badge flips fresh -> delayed -> stale — an honest simulation of an
# upstream outage for one symbol (nothing is faked into the store).
_PAUSED: dict[str, float] = {}


def paused_symbols() -> list[str]:
    return [s for s in list(_PAUSED) if is_paused(s)]


def pause(symbol: str, seconds: float) -> None:
    import time
    _PAUSED[symbol] = time.time() + seconds


def is_paused(symbol: str) -> bool:
    import time
    until = _PAUSED.get(symbol)
    if until is None:
        return False
    if time.time() >= until:
        _PAUSED.pop(symbol, None)
        return False
    return True


async def _unique_symbols(conn: asyncpg.Connection) -> list[str]:
    """Every watched symbol once (fan-in) + the index, which the market-adjusted live feature needs.
    Paused (simulated-outage) symbols are skipped so their data honestly goes stale."""
    rows = await conn.fetch("select distinct symbol from watchlist_items")
    syms = [r["symbol"] for r in rows if not is_paused(r["symbol"])]
    if syms and INDEX_SYMBOL not in syms:
        syms.append(INDEX_SYMBOL)
    return syms


async def poll_once(pool: asyncpg.Pool) -> int:
    """One fan-in cycle: fetch every watched symbol once, append quotes. Returns #symbols polled."""
    provider = get_provider()
    async with pool.acquire() as conn:
        symbols = await _unique_symbols(conn)
        if not symbols:
            return 0
        prev = await quotes.latest_quotes(conn, symbols)  # for single-tick jump sanity checks

    fetched = await provider.get_quotes(symbols)  # one shared upstream call for all users

    # Optional second real feed: cross-check quotes, recorded as role='secondary' (never served).
    secondary: dict[str, quotes.Quote] = {}
    get_secondary = getattr(provider, "get_secondary_quote", None)
    if get_secondary is not None:
        for sym in symbols:
            if sym == INDEX_SYMBOL:
                continue
            q2 = await get_secondary(sym)
            if q2 is not None:
                secondary[sym] = q2

    async with pool.acquire() as conn:
        async with conn.transaction():
            for sym, q in fetched.items():
                await quotes.record_quote(conn, q, prev.get(sym))
            for sym, q2 in secondary.items():
                await quotes.record_quote(conn, q2, None, role="secondary")
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


async def _try_become_leader(lock_conn: asyncpg.Connection) -> bool:
    return bool(await lock_conn.fetchval("select pg_try_advisory_lock($1)", LEADER_LOCK_KEY))


async def run(stop: asyncio.Event) -> None:
    """Poll loop with leader election. Never lets one bad cycle kill the poller; prunes periodically.
    A follower instance just re-checks the lock every interval and takes over if the leader disappears."""
    interval = settings.poll_interval_seconds
    prune_every = max(1, int(settings.prune_interval_seconds / interval))
    cycle = 0
    log.info("poller started (interval=%ss, provider=%s)", interval, get_provider().name)
    import time as _time
    from datetime import datetime, timezone
    lock_conn: asyncpg.Connection | None = None   # dedicated session: advisory locks are per-session
    is_leader = False
    while not stop.is_set():
        try:
            if lock_conn is None or lock_conn.is_closed():
                lock_conn = await asyncpg.connect(settings.database_url)
                is_leader = False
            if not is_leader:
                is_leader = await _try_become_leader(lock_conn)
                STATS["role"] = "leader" if is_leader else "follower"
                if is_leader:
                    STATS["leader_since"] = datetime.now(timezone.utc).isoformat()
                    log.info("poller: acquired leader lock — this instance writes")
            if not is_leader:
                # Another instance holds the lock: do NOT poll (no double writes). Re-check next interval.
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                continue
            t0 = _time.monotonic()
            n = await poll_once(db.pool())
            cycle += 1
            STATS.update(last_poll_at=datetime.now(timezone.utc).isoformat(),
                         last_cycle_seconds=round(_time.monotonic() - t0, 3), last_symbols=n, cycles=cycle)
            if n:
                from . import events
                events.publish({"type": "quotes_updated", "symbols": n, "at": STATS["last_poll_at"]})
            if _time.monotonic() - t0 > interval:
                log.warning("poll cycle (%.1fs) exceeded interval (%ss) — ingestion falling behind", _time.monotonic() - t0, interval)
            if n and cycle % prune_every == 0:
                deleted = await prune(db.pool())
                if deleted:
                    log.info("pruned %s old quote rows", deleted)
        except Exception:  # noqa: BLE001 — a bad cycle must not stop ingestion
            STATS["errors"] = STATS.get("errors", 0) + 1
            log.exception("poll cycle failed; continuing")
            if lock_conn is not None and lock_conn.is_closed():
                is_leader = False  # lost the session => lost the lock; re-elect next loop
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    if lock_conn is not None and not lock_conn.is_closed():
        try:
            if is_leader:
                await lock_conn.execute("select pg_advisory_unlock($1)", LEADER_LOCK_KEY)
        finally:
            await lock_conn.close()
    STATS["role"] = "stopped"
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
