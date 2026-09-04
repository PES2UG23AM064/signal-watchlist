"""DB-backed account lifecycle: register, duplicate refused, sign-in, generic errors, lockout, token
rotation on logout, expiry. Runs against the real Postgres like the other integration tests."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION") != "1" or "dummy" in os.environ.get("DATABASE_URL", ""),
    reason="set INTEGRATION=1 with a real DATABASE_URL to run DB-backed invariants",
)

EMAIL = f"it-{uuid.uuid4().hex[:8]}@test.local"
PASSWORD = "correct horse battery"


def _run(coro):
    return asyncio.run(coro)


async def _flow():
    from app import auth, db
    await db.connect()
    await db.run_migrations()
    try:
        async with db.pool().acquire() as c:
            await c.execute("delete from users where email=$1", EMAIL)

        # Registration rules.
        with pytest.raises(HTTPException) as e:
            await auth.register("not-an-email", PASSWORD)
        assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            await auth.register(EMAIL, "short")
        assert e.value.status_code == 400

        s = await auth.register(EMAIL.upper(), PASSWORD, "  Yash  ")   # email normalized, name trimmed
        assert s.user.email == EMAIL and s.user.display_name == "Yash" and s.token

        with pytest.raises(HTTPException) as e:                        # explicit sign-up: no silent merge
            await auth.register(EMAIL, PASSWORD)
        assert e.value.status_code == 409

        # Sign-in: fresh token each time (rotation), old token dead.
        s2 = await auth.login(EMAIL, PASSWORD)
        assert s2.token != s.token
        me = await auth.current_user(f"Bearer {s2.token}")
        assert me.email == EMAIL
        with pytest.raises(HTTPException) as e:
            await auth.current_user(f"Bearer {s.token}")
        assert e.value.status_code == 401

        # Generic error for unknown email AND wrong password (no account enumeration).
        for bad in (("nobody@test.local", PASSWORD), (EMAIL, "wrong-password-1")):
            with pytest.raises(HTTPException) as e:
                await auth.login(*bad)
            assert e.value.status_code == 401 and e.value.detail == "invalid email or password"

        # Lockout after MAX_ATTEMPTS failures (one failure already counted above).
        for _ in range(auth.MAX_ATTEMPTS - 2):
            with pytest.raises(HTTPException):
                await auth.login(EMAIL, "wrong-password-1")
        with pytest.raises(HTTPException) as e:
            await auth.login(EMAIL, "wrong-password-1")
        assert e.value.status_code == 429
        with pytest.raises(HTTPException) as e:                        # even the RIGHT password is refused while locked
            await auth.login(EMAIL, PASSWORD)
        assert e.value.status_code == 429

        # Unlock (simulate time passing), then logout rotates the token server-side.
        async with db.pool().acquire() as c:
            await c.execute("update users set locked_until=null where email=$1", EMAIL)
        s3 = await auth.login(EMAIL, PASSWORD)
        await auth.logout(s3.user.id)
        with pytest.raises(HTTPException) as e:
            await auth.current_user(f"Bearer {s3.token}")
        assert e.value.status_code == 401

        # Expiry: an old token_issued_at is refused.
        s4 = await auth.login(EMAIL, PASSWORD)
        async with db.pool().acquire() as c:
            await c.execute("update users set token_issued_at = now() - interval '31 days' where email=$1", EMAIL)
        with pytest.raises(HTTPException) as e:
            await auth.current_user(f"Bearer {s4.token}")
        assert e.value.status_code == 401 and "expired" in e.value.detail
    finally:
        async with db.pool().acquire() as c:
            await c.execute("delete from users where email=$1", EMAIL)
        await db.disconnect()


def test_account_lifecycle():
    _run(_flow())
