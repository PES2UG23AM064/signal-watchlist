from __future__ import annotations

from fastapi import APIRouter

from ..auth import login_or_register
from ..models import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    """Log in or register in one step: first sight of a username creates it; later logins verify the PIN."""
    token = await login_or_register(body.username, body.pin)
    return LoginResponse(token=token, username=body.username.strip().lower())
