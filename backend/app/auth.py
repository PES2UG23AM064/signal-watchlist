"""Identity: username + hashed PIN, server-issued bearer session token — with a real lifecycle.

Deliberately not OAuth (documented in README). The PIN exists so cross-device "this watchlist is mine"
is defensible. Lifecycle (a fintech panel will ask):
  * tokens EXPIRE (TOKEN_TTL_DAYS) and are re-issued on login;
  * logout ROTATES the token server-side, so a copied token dies with the session;
  * PIN guessing is THROTTLED: after MAX_PIN_ATTEMPTS failures the account locks for LOCK_MINUTES.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, Header, HTTPException, status

from . import db

TOKEN_TTL_DAYS = 30
MAX_PIN_ATTEMPTS = 5
LOCK_MINUTES = 15


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
    """Return a session token. Creates the account on first sight; verifies the PIN thereafter.
    Every successful login issues a FRESH token (rotation), which also invalidates the previous one."""
    username = username.strip().lower()
    if not username or not pin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "username and pin are required")
    if len(pin) < 4:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "pin must be at least 4 characters")

    now = datetime.now(timezone.utc)
    async with db.pool().acquire() as conn:
        # Atomic create-or-nothing avoids a race where two concurrent first-logins both INSERT.
        created = await conn.fetchrow(
            "insert into users (username, pin_hash, session_token, token_issued_at) values ($1, $2, $3, now()) "
            "on conflict (username) do nothing returning session_token",
            username, _hash_pin(pin), _new_token(),
        )
        if created is not None:
            return created["session_token"]  # brand-new account

        row = await conn.fetchrow(
            "select id, pin_hash, failed_pin_attempts, locked_until from users where username=$1", username)
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid pin")
        if row["locked_until"] is not None and row["locked_until"] > now:
            wait = int((row["locked_until"] - now).total_seconds() // 60) + 1
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"too many attempts; try again in ~{wait} min")

        if not _verify_pin(pin, row["pin_hash"]):
            attempts = row["failed_pin_attempts"] + 1
            lock = now + timedelta(minutes=LOCK_MINUTES) if attempts >= MAX_PIN_ATTEMPTS else None
            await conn.execute("update users set failed_pin_attempts=$2, locked_until=$3 where id=$1",
                               row["id"], 0 if lock else attempts, lock)
            if lock:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"too many attempts; locked for {LOCK_MINUTES} min")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid pin")

        token = _new_token()
        await conn.execute(
            "update users set session_token=$2, token_issued_at=now(), failed_pin_attempts=0, locked_until=null where id=$1",
            row["id"], token)
        return token


async def logout(user_id: str) -> None:
    """Server-side logout: rotate the token so the old one is dead everywhere, not just in this browser."""
    async with db.pool().acquire() as conn:
        await conn.execute("update users set session_token=$2, token_issued_at=now() where id=$1", user_id, _new_token())


async def current_user(authorization: str | None = Header(default=None)) -> User:
    """FastAPI dependency: resolve the bearer token to a user, or 401 (unknown OR expired)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow("select id, username, token_issued_at from users where session_token=$1", token)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if row["token_issued_at"] < datetime.now(timezone.utc) - timedelta(days=TOKEN_TTL_DAYS):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired; log in again")
    return User(id=str(row["id"]), username=row["username"])


CurrentUser = Depends(current_user)
