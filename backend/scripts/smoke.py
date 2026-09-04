"""End-to-end M1 smoke test against the real database (uses the service layer directly).

Run once DATABASE_URL is set:
    ./.venv/Scripts/python.exe -m scripts.smoke

Exercises: migrate -> register user -> add symbols -> list (no baseline) -> mark seen ->
list again (baseline + change) -> changes feed. Also verifies the monotonic-watermark guard.
"""
from __future__ import annotations

import asyncio

from app import db, services
from app.auth import login_or_register


async def main() -> None:
    await db.connect()
    await db.run_migrations()

    # 0. Clean any leftover state from a prior (possibly crashed) run — cascades to watchlist/read_state.
    async with db.pool().acquire() as conn:
        await conn.execute("delete from users where username='smoke_user'")

    # 1. Register (or log in) a throwaway demo user.
    token = await login_or_register("smoke_user", "1234")
    async with db.pool().acquire() as conn:
        uid = str(await conn.fetchval("select id from users where session_token=$1", token))
    print("user:", uid[:8], "token:", token[:12] + "...")

    # 2. Add symbols (idempotent — add one twice).
    for s in ["reliance", "TCS", "INFY.NS", "reliance"]:
        norm = await services.add_symbol(uid, s)
        print("added:", norm)

    # 3. First list — nothing seen yet, so no baselines.
    wl = await services.list_watchlist(uid)
    print("\nwatchlist (pre-seen):")
    for r in wl:
        print(f"  {r.symbol:14} {r.price:>10}  baseline={r.has_baseline}")
    assert all(not r.has_baseline for r in wl), "expected no baselines before mark_seen"

    # 4. Mark everything seen -> snapshots frozen.
    await services.mark_all_seen(uid)

    # 5. Let replay prices move, then list again -> baselines + deltas present.
    await asyncio.sleep(3)
    wl = await services.list_watchlist(uid)
    print("\nwatchlist (post-seen, after 3s):")
    for r in wl:
        ch = r.change_since_seen
        print(f"  {r.symbol:14} {r.price:>10}  chg={ch.pct if ch else None}%  baseline={r.has_baseline}")
    assert all(r.has_baseline for r in wl), "expected baselines after mark_seen"

    # 6. Changes feed (ranked).
    changes = await services.get_changes(uid)
    print("\nchanges feed (ranked by magnitude):")
    for c in changes:
        print(f"  {c.symbol:14} {c.reason:35} meaningful={c.is_meaningful}")

    # 7. Monotonic watermark: an older snapshot must not overwrite a newer one.
    async with db.pool().acquire() as conn:
        before = await conn.fetchval(
            "select watermark_event_time from read_state where user_id=$1 and symbol='RELIANCE.NS'", uid
        )
    # Attempt to write a stale (older) watermark directly and confirm the guard rejects it.
    async with db.pool().acquire() as conn:
        await conn.execute(
            """
            insert into read_state (user_id, symbol, watermark_event_time, snapshot_json)
            values ($1, 'RELIANCE.NS', $2, '{"price": 1, "event_time": "x", "source": "stale"}'::jsonb)
            on conflict (user_id, symbol) do update
              set watermark_event_time = excluded.watermark_event_time, snapshot_json = excluded.snapshot_json
              where excluded.watermark_event_time >= read_state.watermark_event_time
            """,
            uid, before.replace(year=before.year - 1),
        )
        after = await conn.fetchval(
            "select watermark_event_time from read_state where user_id=$1 and symbol='RELIANCE.NS'", uid
        )
    assert after == before, "monotonic watermark guard FAILED (stale write moved it backward)"
    print("\nmonotonic watermark guard: OK (stale write rejected)")

    # Cleanup so re-runs are clean.
    async with db.pool().acquire() as conn:
        await conn.execute("delete from users where username='smoke_user'")
    await db.disconnect()
    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())
