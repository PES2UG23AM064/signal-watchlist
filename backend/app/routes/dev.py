"""Dev/demo endpoints. Auth-scoped (only touch the caller's own state) and Replay-only."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from .. import db, services
from ..auth import CurrentUser, User
from ..baselines import INDEX_SYMBOL
from ..config import settings
from ..providers import get_provider
from ..providers.replay import ReplayProvider

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/rewind")
async def rewind(minutes: int = 15, user: User = CurrentUser) -> dict:
    """Re-create the caller's snapshots AS OF `minutes` ago, using the simulator's deterministic history —
    so 'While you were away' can be demonstrated on demand instead of waiting for a scripted event.
    Replay-only: it is exact there because price is a pure function of time. Bypasses the monotonic
    watermark deliberately (dev tool; the real mark-seen path never does)."""
    if not settings.enable_dev_endpoints:
        raise HTTPException(404, "dev endpoints disabled")
    p = get_provider()
    if not isinstance(p, ReplayProvider):
        raise HTTPException(400, "rewind requires the replay provider (deterministic history)")
    if not 1 <= minutes <= 180:
        raise HTTPException(400, "minutes must be in [1, 180]")

    then = datetime.now(timezone.utc).timestamp() - minutes * 60
    then_dt = datetime.fromtimestamp(then, tz=timezone.utc)
    idx_then = p.price_volume_at(INDEX_SYMBOL, then)[0]
    async with db.pool().acquire() as conn:
        symbols = await services._symbols_for(conn, user.id)
        async with conn.transaction():
            for sym in symbols:
                price_then = p.price_volume_at(sym, then)[0]
                await services.force_snapshot(conn, user.id, sym, price_then, then_dt, idx_then)
    return {"rewound_minutes": minutes, "symbols": len(symbols), "as_of": then_dt.isoformat()}
