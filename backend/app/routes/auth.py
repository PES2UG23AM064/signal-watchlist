"""Accounts: create-account, sign-in, server-side logout, and a `me` probe for remembered sessions."""
from __future__ import annotations

from fastapi import APIRouter, status

from .. import auth
from ..auth import CurrentSession, CurrentUser, User
from ..models import AuthResponse, LoginRequest, MeResponse, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def _resp(s: auth.Session) -> AuthResponse:
    return AuthResponse(token=s.token, email=s.user.email, display_name=s.user.display_name)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest) -> AuthResponse:
    """Create an account. 409 if the email is taken."""
    return _resp(await auth.register(body.email, body.password, body.display_name))


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest) -> AuthResponse:
    """Sign in and issue a session token. 401 on bad credentials; 429 when locked."""
    return _resp(await auth.login(body.email, body.password))


@router.get("/me", response_model=MeResponse)
async def me(user: User = CurrentUser) -> MeResponse:
    return MeResponse(email=user.email, display_name=user.display_name)


@router.post("/logout")
async def logout(everywhere: bool = False, session: auth.Session = CurrentSession) -> dict:
    """End this device's session server-side; other devices stay signed in unless `everywhere=true`."""
    await auth.logout(session.user.id, session.token, everywhere=everywhere)
    return {"ok": True}
