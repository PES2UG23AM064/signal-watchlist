from __future__ import annotations

from fastapi import APIRouter

from ..auth import CurrentUser, User, login_or_register, logout as _logout
from ..models import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/logout")
async def logout(user: User = CurrentUser) -> dict:
    """Rotate the session token server-side — the old token stops working on every device."""
    await _logout(user.id)
    return {"ok": True}


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    """Log in or register in one step: first sight of a username creates it; later logins verify the PIN."""
    token = await login_or_register(body.username, body.pin)
    return LoginResponse(token=token, username=body.username.strip().lower())
