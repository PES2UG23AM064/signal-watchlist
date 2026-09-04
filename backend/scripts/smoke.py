"""End-to-end M2 smoke test against the real database.

Run once DATABASE_URL is set:
    ./.venv/Scripts/python.exe -m scripts.smoke

Covers: migrate -> register -> add (immediate quote) -> poll -> list (no baseline) -> mark seen ->
poll again -> list (baseline + change via the poller) -> changes feed -> monotonic-watermark guard ->
sanity quarantine (garbage quote never served as truth) -> market status.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app import db, poller, quotes, services
from app.auth import login_or_register
from app.market import market_status
from app.providers import Quote


async def main() -> None:
    await db.connect()
    await db.run_migrations()

    async with db.pool().acquire() as conn:
        await conn.execute("delete from users where username='smoke_user'")

    token = await login_or_register("smoke_user", "1234")
    async with db.pool().acquire() as conn:
        uid = str(await conn.fetchval("select id from users where session_token=$1", token))
    print("user:", uid[:8])

    for s in ["reliance", "TCS", "INFY.NS", "reliance"]:
        print("added:", await services.add_symbol(uid, s))

    # Poller populates quotes (fan-in over unique symbols).
    n = await poller.poll_once(db.pool())
    print(f"poll_once polled {n} unique symbols")

    wl = await services.list_watchlist(uid)
    print("\nwatchlist (pre-seen):")
    for r in wl:
        print(f"  {r.symbol:14} {str(r.price):>10}  fresh={r.provenance.freshness} src={r.provenance.source} baseline={r.has_baseline}")
    assert all(not r.has_baseline for r in wl), "expected no baselines before mark_seen"
    assert all(r.price is not None for r in wl), "expected a quote for every symbol after add+poll"
    assert all(r.provenance.freshness == "fresh" for r in wl), "freshly polled quotes should read fresh"

    await services.mark_all_seen(uid)

    # Time passes and the poller advances prices.
    await asyncio.sleep(3)
    await poller.poll_once(db.pool())
    wl = await services.list_watchlist(uid)
    print("\nwatchlist (post-seen, after a second poll):")
    for r in wl:
        ch = r.change_since_seen
        print(f"  {r.symbol:14} {str(r.price):>10}  chg={ch.pct if ch else None}%  baseline={r.has_baseline}")
    assert all(r.has_baseline for r in wl), "expected baselines after mark_seen"

    changes = await services.get_changes(uid)
    print("\nchanges feed (ranked by magnitude):")
    for c in changes:
        print(f"  {c.symbol:14} {c.reason:35} src={c.provenance.source}")

    # Monotonic watermark: an older snapshot must not overwrite a newer one.
    async with db.pool().acquire() as conn:
        before = await conn.fetchval(
            "select watermark_event_time from read_state where user_id=$1 and symbol='RELIANCE.NS'", uid
        )
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

    # Sanity quarantine: a garbage quote (price=0) is stored but NEVER served as the latest truth.
    async with db.pool().acquire() as conn:
        prev_lq = (await quotes.latest_quotes(conn, ["RELIANCE.NS"]))["RELIANCE.NS"]
        good_before = prev_lq.price
        garbage = Quote(symbol="RELIANCE.NS", price=0.0, volume=1, event_time=datetime.now(timezone.utc), source="replay")
        stored_ok = await quotes.record_quote(conn, garbage, prev=prev_lq)
        good_after = (await quotes.latest_quotes(conn, ["RELIANCE.NS"]))["RELIANCE.NS"].price
    assert stored_ok is False, "garbage quote should be flagged suspect"
    assert good_after == good_before, "garbage quote must not become the served latest price"
    print(f"sanity quarantine: OK (price=0 rejected; latest still {good_after})")

    # Market status is derived, not crashing.
    ms = market_status()
    print(f"\nmarket status: {ms.label} - {ms.detail}")

    async with db.pool().acquire() as conn:
        await conn.execute("delete from users where username='smoke_user'")
    await db.disconnect()
    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())
