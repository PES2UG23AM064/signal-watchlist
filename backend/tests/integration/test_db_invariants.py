"""DB-backed integration tests for the integrity invariants the README claims. Run against a REAL
Postgres (the dev Supabase) — skipped unless INTEGRATION=1 and DATABASE_URL is real:

    INTEGRATION=1 ./.venv/Scripts/python.exe -m pytest tests/integration -q

Each test uses a throwaway user/symbol and cleans up after itself."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

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
        await c.execute("delete from users where username='it_user'")
    return db


async def _teardown(db):
    async with db.pool().acquire() as c:
        await c.execute("delete from quotes where symbol=$1", SYM)
        await c.execute("delete from users where username='it_user'")
    await db.disconnect()


def _q(price, dt=None, source="primary-test"):
    from app.providers import Quote
    return Quote(symbol=SYM, price=price, volume=1000, event_time=dt or datetime.now(timezone.utc), source=source)


def test_quarantine_and_roles_never_change_the_served_price():
    async def body():
        from app import quotes
        db = await _setup()
        try:
            async with db.pool().acquire() as c:
                t0 = datetime.now(timezone.utc)
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
                old = datetime.now(timezone.utc) - timedelta(minutes=20)
                await quotes.record_quote(c, _q(300.0, old), None)          # a wrong, STALE reference
                prev = (await quotes.latest_quotes(c, [SYM]))[SYM]
                assert await quotes.record_quote(c, _q(200.0), prev) is True  # -33% vs a 20-min-old ref: accepted
                assert (await quotes.latest_quotes(c, [SYM]))[SYM].price == 200.0
        finally:
            await _teardown(db)
    _run(body())


def test_read_state_watermark_is_monotonic():
    async def body():
        from app import quotes, services
        from app.auth import login_or_register
        db = await _setup()
        try:
            token = await login_or_register("it_user", "1234")
            async with db.pool().acquire() as c:
                uid = str(await c.fetchval("select id from users where session_token=$1", token))
                now = datetime.now(timezone.utc)
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
    async def body():
        from app.config import settings
        from app.poller import LEADER_LOCK_KEY
        a = await asyncpg.connect(settings.database_url)
        b = await asyncpg.connect(settings.database_url)
        try:
            assert await a.fetchval("select pg_try_advisory_lock($1)", LEADER_LOCK_KEY) is True
            assert await b.fetchval("select pg_try_advisory_lock($1)", LEADER_LOCK_KEY) is False  # one leader
            await a.execute("select pg_advisory_unlock($1)", LEADER_LOCK_KEY)
            assert await b.fetchval("select pg_try_advisory_lock($1)", LEADER_LOCK_KEY) is True   # failover
            await b.execute("select pg_advisory_unlock($1)", LEADER_LOCK_KEY)
        finally:
            await a.close(); await b.close()
    _run(body())
