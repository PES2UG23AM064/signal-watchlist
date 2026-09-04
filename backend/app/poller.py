"""Shared per-symbol poller — the ingestion backbone.

Runs as an in-process asyncio task inside FastAPI's lifespan (single Render container; the free tier
has no separate worker type), but is written decoupled so it also runs standalone:  python -m app.poller

Key property — FAN-IN: it polls each UNIQUE symbol once per cycle, no matter how many users watch it
(RELIANCE watched by 500 users = one upstream fetch, not 500). That is the scalability story; a naive
per-user-per-symbol poll is what it deliberately avoids.

Leader election: exactly ONE poller writes even if several instances run — a Postgres session-level
advisory lock held on a dedicated connection (LEADER_LOCK_KEY). The lock is re-verified immediately
before each write, and the unique (symbol, event_time) constraint is the last line of defence if a leader
loses its session mid-write. NOTE: session-level locks need a SESSION-mode connection (Supabase pooler on
5432, not the transaction pooler on 6543, which recycles the physical session between transactions).

Rate awareness: when the active provider is rate-limited (a live feed), the poll interval stretches so
that symbols-per-cycle × cycles-per-minute never exceeds the provider's budget — the loop slows down
honestly instead of wedging behind a token bucket. `/status` shows the effective interval.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

import asyncpg

from . import baselines, db, quotes
from .baselines import INDEX_SYMBOL
from .config import settings
from .providers import get_provider, replay_instance

log = logging.getLogger("poller")

# Observability: what the poller is doing right now (served by GET /status).
STATS: dict = {"last_poll_at": None, "last_cycle_seconds": None, "last_symbols": 0, "cycles": 0, "errors": 0,
               "role": "starting", "leader_since": None, "effective_interval_seconds": None,
               "skipped_writes_lost_lock": 0, "lease_seconds": None}

LEADER_LOCK_KEY = 0x5157_4C53  # arbitrary constant: "signal-watchlist poller"

# Zombie-leader protection. A session-level advisory lock is released when the SESSION ends — but a leader
# whose process died without a clean shutdown (Render free tier spinning down, a laptop sleeping, a TCP
# drop) can leave a half-dead session that Postgres won't notice for minutes. During that window no
# follower can take over and every quote goes stale. So the lock session heartbeats every interval and
# carries a server-side idle_session_timeout: if the leader goes silent for LEASE_S, Postgres itself kills
# the session, the lock drops, and a follower is writing within one interval. Nothing else depends on it.
LEASE_S = 60

# Dev/demo: symbols whose upstream is "down" until the given unix time. The poller skips them, so their
# latest quote AGES and the freshness badge flips fresh -> delayed -> stale — an honest simulation of an
# upstream outage for one symbol (nothing is faked into the store).
_PAUSED: dict[str, float] = {}


def paused_symbols() -> list[str]:
    return [s for s in list(_PAUSED) if is_paused(s)]


def pause(symbol: str, seconds: float) -> None:
    _PAUSED[symbol] = time.time() + seconds


def is_paused(symbol: str) -> bool:
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


def _ensure_replay_profiles(bases: dict[str, baselines.Baselines]) -> None:
    """The simulator must only ever quote from REAL anchors. A symbol can enter the watchlist through
    ANOTHER process (a second API instance, a script against the shared DB) whose add_symbol never reached
    this process's simulator — so the leader re-syncs profiles from the baselines it already loaded for the
    cycle. This is exactly how an earlier deployment came to serve TATASTEEL at 3,185 when the real close
    was 188: an unknown symbol was handed an invented level. Now a symbol has a real anchor or is omitted."""
    rp = replay_instance()
    if rp is None:
        return
    for sym, b in bases.items():
        if not rp.has_profile(sym):
            rp.set_profile(sym, anchor=b.last_close, sigma_daily=b.ret_stdev_daily, avg_vol=b.avg_volume_20d)


async def poll_once(pool: asyncpg.Pool, still_leader: Callable[[], bool] | None = None) -> int:
    """One fan-in cycle: fetch every watched symbol once, append quotes in ONE round-trip.
    `still_leader` is re-checked right before writing so a poller that lost its lock session mid-cycle
    never writes. Returns #symbols polled (0 if the cycle was skipped)."""
    provider = get_provider()
    async with pool.acquire() as conn:
        symbols = await _unique_symbols(conn)
        if not symbols:
            return 0
        prev = await quotes.latest_quotes(conn, symbols)         # recent reference for tick-jump checks
        bases = await baselines.get_baselines(conn, symbols)     # last real close bounds the stale case
    _ensure_replay_profiles(bases)

    fetched = await provider.get_quotes(symbols)  # one shared upstream batch for all users

    # Optional second real feed: concurrent, best-effort cross-checks recorded as role='secondary'.
    secondary: dict[str, quotes.Quote] = {}
    get_secondary_batch = getattr(provider, "get_secondary_quotes", None)
    if get_secondary_batch is not None:
        secondary = await get_secondary_batch([s for s in symbols if s != INDEX_SYMBOL])

    rows: list[tuple] = []
    for sym, q in fetched.items():
        p = prev.get(sym)
        anchor = bases[sym].last_close if sym in bases else None
        suspect = quotes.is_suspect(q, p.price if p else None,
                                    prev_event_time=p.event_time if p else None, anchor=anchor)
        rows.append((q.symbol, q.price, q.volume, q.event_time, q.source, suspect, "primary"))
    for q2 in secondary.values():
        rows.append((q2.symbol, q2.price, q2.volume, q2.event_time, q2.source, quotes.is_suspect(q2, None), "secondary"))

    if still_leader is not None and not still_leader():
        STATS["skipped_writes_lost_lock"] += 1
        log.warning("lost the leader lock during the cycle — skipping write (%d rows)", len(rows))
        return 0

    # ONE round-trip for all writes: per-row INSERTs cost a network hop each (~200ms cross-region), which
    # at 30+ symbols pushed a cycle past the poll interval. executemany pipelines the batch.
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                "insert into quotes (symbol, price, volume, event_time, source, is_suspect, role) "
                "values ($1, $2, $3, $4, $5, $6, $7) on conflict (symbol, event_time) do nothing",
                rows,
            )
    return len(symbols)


async def prune(pool: asyncpg.Pool) -> int:
    """Bounded retention so the time-series never grows without limit."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "delete from quotes where event_time < now() - ($1 || ' hours')::interval",
            str(settings.quote_retention_hours),
        )
    try:
        return int(result.split()[-1])  # "DELETE 42"
    except (ValueError, IndexError):
        return 0


def _min_interval_for(provider, n_symbols: int) -> float:
    """Seconds per cycle needed to stay inside a rate-limited provider's budget (0 if unlimited or if a
    composite is currently serving from its fallback and won't touch the live feed)."""
    rate = getattr(provider, "rate_per_min", None)
    if rate is None and hasattr(provider, "primary"):
        if hasattr(provider, "serving_live") and not provider.serving_live():
            return 0.0
        rate = getattr(provider.primary, "rate_per_min", None)
    if not rate:
        return 0.0
    return n_symbols * 60.0 / float(rate)


async def _try_become_leader(lock_conn: asyncpg.Connection) -> bool:
    return bool(await lock_conn.fetchval("select pg_try_advisory_lock($1)", LEADER_LOCK_KEY))


async def _open_lock_session() -> asyncpg.Connection:
    """The dedicated lock session, with the lease: Postgres ends it after LEASE_S of silence."""
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(f"set idle_session_timeout = '{LEASE_S * 1000}'")
        STATS["lease_seconds"] = LEASE_S
    except asyncpg.PostgresError as exc:  # pre-PG14 has no such GUC; the lock still works, minus the lease
        STATS["lease_seconds"] = None
        log.warning("idle_session_timeout unavailable (%s): a crashed leader's lock may linger", exc)
    return conn


async def run(stop: asyncio.Event) -> None:
    """Poll loop with leader election. Never lets one bad cycle kill the poller; prunes periodically.
    A follower instance just re-checks the lock every interval and takes over if the leader disappears."""
    interval = settings.poll_interval_seconds
    prune_every = max(1, int(settings.prune_interval_seconds / interval))
    cycle = 0
    provider = get_provider()
    log.info("poller started (interval=%ss, provider=%s)", interval, provider.name)
    lock_conn: asyncpg.Connection | None = None   # dedicated session: advisory locks are per-session
    is_leader = False
    stretched_logged = False

    def still_leader() -> bool:
        return is_leader and lock_conn is not None and not lock_conn.is_closed()

    async def heartbeat() -> None:
        """Prove the lock session is alive (and keep its lease). A failure here means the session — and
        with it the lock — is gone: drop leadership now rather than after a silent write."""
        nonlocal is_leader, lock_conn
        if lock_conn is None or lock_conn.is_closed():
            return
        try:
            await lock_conn.execute("select 1")
        except Exception as exc:  # noqa: BLE001 — any failure on the lock session means re-elect
            log.warning("lock session lost (%s) — re-electing", exc)
            is_leader = False
            STATS["role"] = "follower"
            try:
                await lock_conn.close()
            except Exception:  # noqa: BLE001
                pass
            lock_conn = None

    async def sleep(seconds: float) -> None:
        """Wait, but never let the lock session go quiet longer than one interval (see LEASE_S)."""
        deadline = time.monotonic() + seconds
        while not stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=min(interval, remaining))
            except TimeoutError:
                pass
            if not stop.is_set():
                await heartbeat()

    wait = interval
    while not stop.is_set():
        try:
            if lock_conn is None or lock_conn.is_closed():
                lock_conn = await _open_lock_session()
                is_leader = False
            if not is_leader:
                is_leader = await _try_become_leader(lock_conn)
                STATS["role"] = "leader" if is_leader else "follower"
                if is_leader:
                    STATS["leader_since"] = datetime.now(UTC).isoformat()
                    log.info("poller: acquired leader lock — this instance writes")
            if not is_leader:
                # Another instance holds the lock: do NOT poll (no double writes). Re-check next interval.
                await sleep(interval)
                continue

            await heartbeat()          # a leader whose session died must not write this cycle
            if not is_leader:
                continue

            t0 = time.monotonic()
            n = await poll_once(db.pool(), still_leader)
            cycle += 1
            elapsed = time.monotonic() - t0
            wait = max(interval, _min_interval_for(provider, n))
            STATS.update(last_poll_at=datetime.now(UTC).isoformat(), last_cycle_seconds=round(elapsed, 3),
                         last_symbols=n, cycles=cycle, effective_interval_seconds=round(wait, 1))
            if wait > interval and not stretched_logged:
                log.info("poll interval stretched to %.1fs: %d symbols against the live feed's rate budget", wait, n)
                stretched_logged = True
            if n:
                from . import events
                events.publish({"type": "quotes_updated", "symbols": n, "at": STATS["last_poll_at"]})
            if elapsed > wait:
                log.warning("poll cycle (%.1fs) exceeded interval (%ss) — ingestion falling behind", elapsed, wait)
            if n and cycle % prune_every == 0:
                deleted = await prune(db.pool())
                if deleted:
                    log.info("pruned %s old quote rows", deleted)
        except Exception:  # noqa: BLE001 — a bad cycle must not stop ingestion
            STATS["errors"] = STATS.get("errors", 0) + 1
            log.exception("poll cycle failed; continuing")
            if lock_conn is not None and lock_conn.is_closed():
                is_leader = False  # lost the session => lost the lock; re-elect next loop
        await sleep(wait)

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
