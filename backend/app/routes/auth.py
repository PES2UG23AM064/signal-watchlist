"""Accounts: explicit create-account and sign-in (never merged — a typo must not create a ghost account),
server-side logout, and a `me` probe the client uses to validate a remembered session on load."""
from __future__ import annotations

from fastapi import APIRouter, status

from .. import auth
from ..auth import CurrentUser, User
from ..models import AuthResponse, LoginRequest, MeResponse, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def _resp(s: auth.Session) -> AuthResponse:
    return AuthResponse(token=s.token, email=s.user.email, display_name=s.user.display_name)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest) -> AuthResponse:
    """Create an account (email + password ≥ 8 chars, optional display name). 409 if the email is taken."""
    return _resp(await auth.register(body.email, body.password, body.display_name))


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest) -> AuthResponse:
    """Sign in. Issues a fresh session token (rotation). 401 generic on bad credentials; 429 when locked."""
    return _resp(await auth.login(body.email, body.password))


@router.get("/me", response_model=MeResponse)
async def me(user: User = CurrentUser) -> MeResponse:
    """Who am I — lets a returning device confirm its remembered token is still valid before rendering."""
    return MeResponse(email=user.email, display_name=user.display_name)


@router.post("/logout")
async def logout(user: User = CurrentUser) -> dict:
    """Rotate the session token server-side — the old token stops working on every device."""
    await auth.logout(user.id)
    return {"ok": True}
