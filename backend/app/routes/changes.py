from __future__ import annotations

from fastapi import APIRouter

from .. import services
from ..auth import CurrentUser, User
from ..models import ChangeRow

router = APIRouter(tags=["changes"])


@router.get("/changes", response_model=list[ChangeRow])
async def get_changes(user: User = CurrentUser) -> list[ChangeRow]:
    """'While you were away': symbols that moved since the user's snapshot, ranked by magnitude."""
    return await services.get_changes(user.id)
