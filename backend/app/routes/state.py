from __future__ import annotations

from fastapi import APIRouter

from .. import services
from ..auth import CurrentUser, User
from ..market import market_status
from ..models import MarketStatusModel, StateResponse

router = APIRouter(tags=["state"])


@router.get("/state", response_model=StateResponse)
async def get_state(user: User = CurrentUser) -> StateResponse:
    """One round trip for the whole dashboard: market status + watchlist + ranked changes,
    computed in a single pass (no duplicate queries)."""
    items, changes, cohorts = await services.get_state(user.id)
    s = market_status()
    return StateResponse(
        market=MarketStatusModel(is_open=s.is_open, label=s.label, detail=s.detail),
        items=items,
        changes=changes,
        cohorts=cohorts,
    )
