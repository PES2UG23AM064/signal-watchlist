"""Minimal identity: username + hashed PIN, server-issued bearer session token.

Deliberately not OAuth (documented in README). The PIN exists so cross-device "this watchlist is
mine" is defensible — you prove it's you on a new device, not just by typing a known username.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

import bcrypt
from fastapi import Depends, Header, HTTPException, status

from . import db


def _hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_pin(pin: str, pin_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pin.encode("utf-8"), pin_hash.encode("utf-8"))
    except ValueError:
        return False


@dataclass
class User:
    id: str
    username: str


def _new_token() -> str:
    return secrets.token_urlsafe(32)


async def login_or_register(username: str, pin: str) -> str:
    """Return a session token. Creates the account on first sight; verifies the PIN thereafter."""
    username = username.strip().lower()
    if not username or not pin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "username and pin are required")

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow("select id, pin_hash, session_token from users where username=$1", username)
        if row is None:
            token = _new_token()
            await conn.execute(
                "insert into users (username, pin_hash, session_token) values ($1, $2, $3)",
                username, _hash_pin(pin), token,
            )
            return token
        if not _verify_pin(pin, row["pin_hash"]):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid pin")
        return row["session_token"]


async def current_user(authorization: str | None = Header(default=None)) -> User:
    """FastAPI dependency: resolve the bearer token to a user, or 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow("select id, username from users where session_token=$1", token)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    return User(id=str(row["id"]), username=row["username"])


CurrentUser = Depends(current_user)
