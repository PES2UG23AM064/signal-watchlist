"""Dev/demo endpoints. Auth-scoped (only touch the caller's own state) and Replay-only."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException

from .. import db, poller, quotes, services
from ..auth import CurrentUser, User
from ..baselines import INDEX_SYMBOL
from ..config import settings
from ..models import InjectRequest
from ..providers import Quote, get_provider, replay_instance
from ..providers.composite import CompositeProvider
from ..symbols import normalize

router = APIRouter(prefix="/dev", tags=["dev"])

STALE_OUTAGE_S = 150  # long enough for the badge to pass 'delayed' (>20s) and reach 'stale' (>120s)


@router.post("/inject")
async def inject(body: InjectRequest, user: User = CurrentUser) -> dict:
    """Fault injection through the real ingestion path.
    garbage: price=0 tick; jump: price x1.35 (beyond NSE's 20% band); future: event_time +1h;
    stale: pause this symbol's upstream so its data ages; conflict: a divergent secondary quote."""
    if not settings.enable_dev_endpoints:
        raise HTTPException(404, "dev endpoints disabled")
    symbol = normalize(body.symbol)
    now = datetime.now(UTC)

    if body.kind == "stale":
        poller.pause(symbol, STALE_OUTAGE_S)
        return {"symbol": symbol, "kind": "stale", "effect": f"upstream paused {STALE_OUTAGE_S}s; watch the freshness badge age"}

    async with db.pool().acquire() as conn:
        prev = (await quotes.latest_quotes(conn, [symbol])).get(symbol)
        if prev is None:
            raise HTTPException(400, "no quote for symbol yet")
        if body.kind == "conflict":
            # Recorded as role='secondary', so it can never become the served price.
            q2 = Quote(symbol=symbol, price=round(prev.price * 1.05, 2), volume=prev.volume, event_time=now,
                       source="injected-secondary")
            await quotes.record_quote(conn, q2, None, role="secondary")
            served = (await quotes.latest_quotes(conn, [symbol]))[symbol]
            return {"symbol": symbol, "kind": "conflict", "injected_price": q2.price, "role": "secondary",
                    "quarantined": False, "served_price_still": served.price,
                    "served_unchanged": abs(served.price - prev.price) < 1e-9,
                    "effect": "divergent secondary quote recorded -> symbol is DISPUTED; primary still served"}
        if body.kind == "garbage":
            q = Quote(symbol=symbol, price=0.0, volume=prev.volume, event_time=now, source="injected")
        elif body.kind == "jump":
            q = Quote(symbol=symbol, price=round(prev.price * 1.35, 2), volume=prev.volume, event_time=now, source="injected")
        elif body.kind == "future":
            q = Quote(symbol=symbol, price=prev.price, volume=prev.volume, event_time=now + timedelta(hours=1), source="injected")
        else:
            raise HTTPException(400, "kind must be garbage | jump | future | stale | conflict")
        accepted = await quotes.record_quote(conn, q, prev)
        served = (await quotes.latest_quotes(conn, [symbol]))[symbol]
    return {"symbol": symbol, "kind": body.kind, "injected_price": q.price, "quarantined": not accepted,
            "served_price_still": served.price, "served_unchanged": abs(served.price - prev.price) < 1e-9}


@router.post("/rewind")
async def rewind(minutes: int = 15, user: User = CurrentUser) -> dict:
    """Re-create the caller's snapshots as of `minutes` ago from the simulator's deterministic history.
    Replay-only, and deliberately bypasses the monotonic watermark."""
    if not settings.enable_dev_endpoints:
        raise HTTPException(404, "dev endpoints disabled")
    p = replay_instance()
    if p is None:
        raise HTTPException(400, "rewind requires the replay simulator (deterministic history)")
    active = get_provider()
    if isinstance(active, CompositeProvider) and active.serving_live():
        # Served prices are live right now; reconstructing "then" from the simulator would be inconsistent.
        raise HTTPException(400, "rewind is only available while the simulator is the active source (market closed or live feed down)")
    if not 1 <= minutes <= 180:
        raise HTTPException(400, "minutes must be in [1, 180]")

    then = datetime.now(UTC).timestamp() - minutes * 60
    then_dt = datetime.fromtimestamp(then, tz=UTC)
    idx_then = p.price_volume_at(INDEX_SYMBOL, then)[0]
    async with db.pool().acquire() as conn:
        symbols = await services._symbols_for(conn, user.id)
        async with conn.transaction():
            rewound = 0
            for sym in symbols:
                if not p.can_quote(sym):   # no real anchor, nothing to reconstruct
                    continue
                price_then = p.price_volume_at(sym, then)[0]
                await services.force_snapshot(conn, user.id, sym, price_then, then_dt, idx_then)
                rewound += 1
    return {"rewound_minutes": minutes, "symbols": rewound, "as_of": then_dt.isoformat()}
