"""DB-backed integration tests for the integrity invariants the README claims. Run against a REAL
Postgres (the dev Supabase) — skipped unless INTEGRATION=1 and DATABASE_URL is real:

    INTEGRATION=1 ./.venv/Scripts/python.exe -m pytest tests/integration -q

Each test uses a throwaway user/symbol and cleans up after itself."""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION") != "1" or "dummy" in os.environ.get("DATABASE_URL", ""),
    reason="set INTEGRATION=1 with a real DATABASE_URL to run DB-backed invariants",
)

SYM = "ITEST.NS"


def _run(coro):
    return asyncio.run(coro)


async def _setup():
    from app import db
    await db.connect()
    await db.run_migrations()
    async with db.pool().acquire() as c:
        await c.execute("delete from quotes where symbol=$1", SYM)
        await c.execute("delete from users where email='it_user@test.local'")
    return db


async def _teardown(db):
    async with db.pool().acquire() as c:
        await c.execute("delete from quotes where symbol=$1", SYM)
        await c.execute("delete from users where email='it_user@test.local'")
    await db.disconnect()


def _q(price, dt=None, source="primary-test"):
    from app.providers import Quote
    return Quote(symbol=SYM, price=price, volume=1000, event_time=dt or datetime.now(UTC), source=source)


def test_quarantine_and_roles_never_change_the_served_price():
    async def body():
        from app import quotes
        db = await _setup()
        try:
            async with db.pool().acquire() as c:
                t0 = datetime.now(UTC)
                assert await quotes.record_quote(c, _q(100.0, t0), None) is True
                prev = (await quotes.latest_quotes(c, [SYM]))[SYM]
                # garbage, absurd jump, future timestamp -> all quarantined
                assert await quotes.record_quote(c, _q(0.0, t0 + timedelta(seconds=1)), prev) is False
                assert await quotes.record_quote(c, _q(135.0, t0 + timedelta(seconds=2)), prev) is False
                assert await quotes.record_quote(c, _q(100.0, t0 + timedelta(hours=1)), prev) is False
                # a secondary-feed quote is stored but is not the served price
                await quotes.record_quote(c, _q(106.0, t0 + timedelta(seconds=3), "secondary-test"), None, role="secondary")
                served = (await quotes.latest_quotes(c, [SYM]))[SYM]
                assert served.price == 100.0 and served.source == "primary-test"
                # ...and the divergence IS surfaced as a dispute
                d = await quotes.disputes(c, [SYM], threshold_pct=2.0, window_s=600)
                assert SYM in d and d[SYM]["secondary_price"] == 106.0 and d[SYM]["divergence_pct"] == 6.0
                # a secondary within tolerance is not a dispute
                await quotes.record_quote(c, _q(101.0, t0 + timedelta(seconds=4), "secondary-test"), None, role="secondary")
                assert SYM not in await quotes.disputes(c, [SYM], threshold_pct=2.0, window_s=600)
        finally:
            await _teardown(db)
    _run(body())


def test_stale_reference_does_not_quarantine_correct_data_forever():
    async def body():
        from app import quotes
        db = await _setup()
        try:
            async with db.pool().acquire() as c:
                old = datetime.now(UTC) - timedelta(minutes=20)
                await quotes.record_quote(c, _q(300.0, old), None)          # a wrong, STALE reference
                prev = (await quotes.latest_quotes(c, [SYM]))[SYM]
                assert await quotes.record_quote(c, _q(200.0), prev) is True  # -33% vs a 20-min-old ref: accepted
                assert (await quotes.latest_quotes(c, [SYM]))[SYM].price == 200.0
        finally:
            await _teardown(db)
    _run(body())


def test_read_state_watermark_is_monotonic():
    async def body():
        from app import auth, quotes, services
        db = await _setup()
        try:
            uid = (await auth.register("it_user@test.local", "it-password-1")).user.id
            async with db.pool().acquire() as c:
                now = datetime.now(UTC)
                await quotes.record_quote(c, _q(100.0, now), None)
                lq_new = (await quotes.latest_quotes(c, [SYM]))[SYM]
                await services._snapshot_upsert(c, uid, SYM, lq_new, None)
                # an OLDER snapshot must not overwrite the newer baseline
                lq_old = quotes.LatestQuote(symbol=SYM, price=90.0, volume=1, event_time=now - timedelta(hours=1), source="x")
                await services._snapshot_upsert(c, uid, SYM, lq_old, None)
                snap = await c.fetchrow("select watermark_event_time, snapshot_json from read_state where user_id=$1 and symbol=$2", uid, SYM)
                assert snap["watermark_event_time"] == now and '"price": 100.0' in snap["snapshot_json"]
        finally:
            await _teardown(db)
    _run(body())


def test_poller_leader_lock_is_exclusive_across_sessions():
    """Session-level advisory lock semantics: one holder at a time, and a waiting session takes over the
    moment the holder releases. Uses a SCRATCH key so the test never contends with a running deployment's
    REAL leader lock — which would (correctly) refuse us; that case is reported, not asserted."""
    async def body():
        from app.config import settings
        from app.poller import LEADER_LOCK_KEY
        scratch = LEADER_LOCK_KEY + 1
        a = await asyncpg.connect(settings.database_url)
        b = await asyncpg.connect(settings.database_url)
        try:
            assert await a.fetchval("select pg_try_advisory_lock($1)", scratch) is True
            assert await b.fetchval("select pg_try_advisory_lock($1)", scratch) is False  # exclusive
            await a.execute("select pg_advisory_unlock($1)", scratch)
            assert await b.fetchval("select pg_try_advisory_lock($1)", scratch) is True   # failover
            await b.execute("select pg_advisory_unlock($1)", scratch)
            # The REAL key: if a deployed poller is live it holds this and we are refused — the whole point.
            got_real = await a.fetchval("select pg_try_advisory_lock($1)", LEADER_LOCK_KEY)
            if got_real:
                await a.execute("select pg_advisory_unlock($1)", LEADER_LOCK_KEY)  # no live leader; hand it back
            print("real leader lock held by a running instance:", not got_real)
        finally:
            await a.close(); await b.close()
    _run(body())
