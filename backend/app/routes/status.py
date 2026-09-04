"""Observability: provider route and breaker, poller state, per-symbol freshness, recent quarantines."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from .. import db, poller, quotes
from ..baselines import INDEX_SYMBOL
from ..config import settings
from ..market import market_status
from ..providers import provider_status

router = APIRouter(tags=["meta"])


@router.get("/status")
async def status() -> dict:
    now = datetime.now(UTC)
    async with db.pool().acquire() as conn:
        symbols = [r["symbol"] for r in await conn.fetch("select distinct symbol from watchlist_items order by symbol")]
        latest = await quotes.latest_quotes(conn, symbols + [INDEX_SYMBOL]) if symbols else {}
        quarantined_hour = await conn.fetchval("select count(*) from quotes where is_suspect and received_at > now() - interval '1 hour'")
        quote_rows = await conn.fetchval("select count(*) from quotes")
    m = market_status()
    return {
        "time": now.isoformat(),
        "market": {"is_open": m.is_open, "label": m.label, "detail": m.detail},
        "provider": provider_status(),
        "poller": {**poller.STATS, "interval_seconds": settings.poll_interval_seconds,
                   "enabled": settings.run_poller, "paused_symbols": poller.paused_symbols(),
                   "leader_election": "postgres advisory lock (session-level, dedicated connection)"},
        "data": {
            "watched_symbols": len(symbols), "quote_rows": int(quote_rows or 0),
            "quarantined_last_hour": int(quarantined_hour or 0),
            "freshness": [
                {"symbol": s, "age_seconds": round(lq.age_seconds(now), 1), "freshness": lq.freshness(now), "source": lq.source}
                for s, lq in sorted(latest.items())
            ],
        },
    }
