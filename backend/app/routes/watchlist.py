from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from .. import services
from ..auth import CurrentUser, User
from ..models import AddSymbolRequest, SetQuantityRequest, WatchRow

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchRow])
async def get_watchlist(user: User = CurrentUser) -> list[WatchRow]:
    return await services.list_watchlist(user.id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_symbol(body: AddSymbolRequest, user: User = CurrentUser) -> dict:
    """Add (idempotent). The moment you add a symbol is your first look at it: that price is the baseline."""
    try:
        symbol = await services.add_symbol(user.id, body.symbol)
    except services.UnknownSymbol as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{e} isn't a symbol we can find on NSE") from e
    except services.SymbolUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"can't get a price for {e} right now — try again in a minute") from e
    return {"symbol": symbol}


@router.delete("/{symbol}")
async def remove_symbol(symbol: str, user: User = CurrentUser) -> dict:
    if not await services.remove_symbol(user.id, symbol):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "symbol is not on your watchlist")
    return {"ok": True}


@router.patch("/{symbol}/quantity")
async def set_quantity(symbol: str, body: SetQuantityRequest, user: User = CurrentUser) -> dict:
    """Record how much of this symbol you hold (or clear it). Held symbols rank by rupees at stake."""
    if not await services.set_quantity(user.id, symbol, body.quantity):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "symbol is not on your watchlist")
    return {"ok": True}


@router.post("/{symbol}/snooze")
async def snooze(symbol: str, minutes: int | None = 60, user: User = CurrentUser) -> dict:
    """Hold this symbol out of 'needs your attention' for N minutes (minutes=0 clears). Never hides it."""
    if not await services.snooze(user.id, symbol, minutes or None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "symbol is not on your watchlist")
    return {"ok": True}


@router.post("/{symbol}/seen")
async def mark_seen(symbol: str, user: User = CurrentUser) -> dict:
    if not await services.mark_seen(user.id, symbol):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "symbol is not on your watchlist")
    return {"ok": True}


@router.post("/seen-all")
async def mark_all_seen(user: User = CurrentUser) -> dict:
    await services.mark_all_seen(user.id)
    return {"ok": True}
