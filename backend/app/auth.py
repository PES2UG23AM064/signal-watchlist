"""Identity: email + password, server-issued bearer session token — with a real lifecycle.

Explicit "create account" vs "sign in" (a typo in your email must never silently create a second, empty
account). Deliberately not OAuth (documented in README). Lifecycle (a fintech panel will ask):
  * passwords are bcrypt-hashed (cost 12); never stored or logged in clear;
  * tokens EXPIRE (TOKEN_TTL_DAYS) and are re-issued on every login (rotation);
  * logout ROTATES the token server-side, so a copied token dies with the session on every device;
  * guessing is THROTTLED: after MAX_ATTEMPTS failures the account locks for LOCK_MINUTES;
  * sign-in errors are deliberately generic ("invalid email or password") so the endpoint does not
    reveal which emails have accounts.
bcrypt is ~100ms of CPU: it runs in a worker thread so it never stalls the event loop (and the SSE
streams and other requests riding on it), and no pooled DB connection is held while it runs.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import anyio
import bcrypt
from fastapi import Depends, Header, HTTPException, status

from . import db

TOKEN_TTL_DAYS = 30
MAX_ATTEMPTS = 5
LOCK_MINUTES = 15
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128   # bcrypt truncates at 72 bytes; refuse absurd inputs before hashing
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class User:
    id: str
    email: str
    display_name: str | None = None


@dataclass
class Session:
    token: str
    user: User


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "email is required")
    return email


def validate_new_credentials(email: str, password: str, display_name: str | None) -> str | None:
    """Registration-time rules (sign-in never re-validates, so legacy accounts keep working)."""
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "enter a valid email address")
    if not password or len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password) > MAX_PASSWORD_LEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "password is too long")
    name = (display_name or "").strip() or None
    if name is not None and len(name) > 60:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name is too long (60 characters max)")
    return name


async def register(email: str, password: str, display_name: str | None = None) -> Session:
    """Create an account. 409 if the email is taken (two concurrent first sign-ups race here; the unique
    constraint makes exactly one INSERT win). Hashing happens off the event loop, before any connection."""
    email = normalize_email(email)
    name = validate_new_credentials(email, password, display_name)
    password_hash = await anyio.to_thread.run_sync(_hash, password)
    token = _new_token()
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "insert into users (email, password_hash, display_name, session_token, token_issued_at) "
            "values ($1, $2, $3, $4, now()) on conflict (email) do nothing returning id",
            email, password_hash, name, token,
        )
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "an account with this email already exists — sign in instead")
    return Session(token=token, user=User(id=str(row["id"]), email=email, display_name=name))


async def login(email: str, password: str) -> Session:
    """Verify credentials and issue a FRESH token (rotation invalidates the previous one)."""
    email = normalize_email(email)
    if not password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "password is required")
    now = datetime.now(UTC)
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "select id, password_hash, display_name, failed_attempts, locked_until from users where email=$1", email)
    if row is None:
        # Same cost/shape as a wrong password so the response does not reveal whether the email exists.
        await anyio.to_thread.run_sync(_verify, password, _DUMMY_HASH)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    if row["locked_until"] is not None and row["locked_until"] > now:
        wait = int((row["locked_until"] - now).total_seconds() // 60) + 1
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"too many attempts; try again in ~{wait} min")

    ok = await anyio.to_thread.run_sync(_verify, password, row["password_hash"])  # off the event loop
    async with db.pool().acquire() as conn:
        if not ok:
            attempts = row["failed_attempts"] + 1
            lock = now + timedelta(minutes=LOCK_MINUTES) if attempts >= MAX_ATTEMPTS else None
            await conn.execute("update users set failed_attempts=$2, locked_until=$3 where id=$1",
                               row["id"], 0 if lock else attempts, lock)
            if lock:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"too many attempts; locked for {LOCK_MINUTES} min")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
        token = _new_token()
        await conn.execute(
            "update users set session_token=$2, token_issued_at=now(), failed_attempts=0, locked_until=null where id=$1",
            row["id"], token)
    return Session(token=token, user=User(id=str(row["id"]), email=email, display_name=row["display_name"]))


# A real bcrypt hash of a random string: used to equalize timing for unknown emails (never matches).
_DUMMY_HASH = _hash(secrets.token_urlsafe(16))


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
        row = await conn.fetchrow(
            "select id, email, display_name, token_issued_at from users where session_token=$1", token)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if row["token_issued_at"] < datetime.now(UTC) - timedelta(days=TOKEN_TTL_DAYS):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired; sign in again")
    return User(id=str(row["id"]), email=row["email"], display_name=row["display_name"])


CurrentUser = Depends(current_user)
