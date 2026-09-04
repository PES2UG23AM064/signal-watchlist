from __future__ import annotations

from fastapi import APIRouter, status

from .. import services
from ..auth import CurrentUser, User
from ..models import AddSymbolRequest, WatchRow

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchRow])
async def get_watchlist(user: User = CurrentUser) -> list[WatchRow]:
    return await services.list_watchlist(user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_symbol(body: AddSymbolRequest, user: User = CurrentUser) -> dict:
    symbol = await services.add_symbol(user.id, body.symbol)
    return {"symbol": symbol}


@router.delete("/{symbol}")
async def remove_symbol(symbol: str, user: User = CurrentUser) -> dict:
    await services.remove_symbol(user.id, symbol)
    return {"ok": True}


@router.post("/{symbol}/seen")
async def mark_seen(symbol: str, user: User = CurrentUser) -> dict:
    await services.mark_seen(user.id, symbol)
    return {"ok": True}


@router.post("/seen-all")
async def mark_all_seen(user: User = CurrentUser) -> dict:
    await services.mark_all_seen(user.id)
    return {"ok": True}
