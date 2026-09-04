"""Identity: email + password, server-issued bearer session tokens (one per device, stored hashed).

"Create account" and "sign in" are separate so a typo never silently creates a second account.
Sign-in errors are generic so the endpoint does not reveal which emails exist.
bcrypt is ~100ms of CPU: it runs in a worker thread with no pooled DB connection held.
"""
from __future__ import annotations

import hashlib
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


BCRYPT_ROUNDS = 10  # OWASP's floor; cost 12 was ~2s per sign-in on a free-tier vCPU


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def _verify(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _token_hash(token: str) -> str:
    """Sessions store a SHA-256 of the token: the table can leak without leaking logins."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _open_session(conn, user_id: str) -> str:
    """Issue a token for this device; other devices' sessions are untouched.
    Expired sessions for the user are swept here so the table cannot grow without bound."""
    token = _new_token()
    await conn.execute("delete from sessions where user_id=$1 and issued_at < now() - make_interval(days => $2)",
                       user_id, TOKEN_TTL_DAYS)
    await conn.execute("insert into sessions (token_hash, user_id) values ($1, $2)", _token_hash(token), user_id)
    return token


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
    """Create an account; 409 if the email is taken (the unique constraint makes exactly one INSERT win)."""
    email = normalize_email(email)
    name = validate_new_credentials(email, password, display_name)
    password_hash = await anyio.to_thread.run_sync(_hash, password)
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "insert into users (email, password_hash, display_name, token_issued_at) "
            "values ($1, $2, $3, now()) on conflict (email) do nothing returning id",
            email, password_hash, name,
        )
        if row is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "an account with this email already exists — sign in instead")
        token = await _open_session(conn, row["id"])
    return Session(token=token, user=User(id=str(row["id"]), email=email, display_name=name))


async def login(email: str, password: str) -> Session:
    """Verify credentials and open a session for this device; other devices stay signed in."""
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

    ok = await anyio.to_thread.run_sync(_verify, password, row["password_hash"])
    async with db.pool().acquire() as conn:
        if not ok:
            attempts = row["failed_attempts"] + 1
            lock = now + timedelta(minutes=LOCK_MINUTES) if attempts >= MAX_ATTEMPTS else None
            await conn.execute("update users set failed_attempts=$2, locked_until=$3 where id=$1",
                               row["id"], 0 if lock else attempts, lock)
            if lock:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"too many attempts; locked for {LOCK_MINUTES} min")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
        await conn.execute("update users set failed_attempts=0, locked_until=null where id=$1", row["id"])
        token = await _open_session(conn, row["id"])
    return Session(token=token, user=User(id=str(row["id"]), email=email, display_name=row["display_name"]))


# Real bcrypt hash of a random string: equalizes timing for unknown emails (never matches).
_DUMMY_HASH = _hash(secrets.token_urlsafe(16))


async def logout(user_id: str, token: str, everywhere: bool = False) -> None:
    """Delete this session server-side (so a copied token dies too); `everywhere` ends all of them."""
    async with db.pool().acquire() as conn:
        if everywhere:
            await conn.execute("delete from sessions where user_id=$1", user_id)
        else:
            await conn.execute("delete from sessions where user_id=$1 and token_hash=$2", user_id, _token_hash(token))


async def current_session(authorization: str | None = Header(default=None)) -> Session:
    """FastAPI dependency: resolve the bearer token to a Session, or 401 (unknown or expired)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "select u.id, u.email, u.display_name, s.issued_at from sessions s join users u on u.id = s.user_id "
            "where s.token_hash=$1", _token_hash(token))
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if row["issued_at"] < datetime.now(UTC) - timedelta(days=TOKEN_TTL_DAYS):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired; sign in again")
    return Session(token=token, user=User(id=str(row["id"]), email=row["email"], display_name=row["display_name"]))


async def current_user(authorization: str | None = Header(default=None)) -> User:
    return (await current_session(authorization)).user


CurrentUser = Depends(current_user)
CurrentSession = Depends(current_session)
