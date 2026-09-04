"""DB-backed account lifecycle: register, duplicate refused, multi-device sign-in, generic errors,
lockout, logout (one device / everywhere), expiry. Same INTEGRATION=1 gate as test_db_invariants."""
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

        # Sign-in on a second device issues a fresh token and leaves the first device signed in.
        s2 = await auth.login(EMAIL, PASSWORD)
        assert s2.token != s.token
        assert (await auth.current_user(f"Bearer {s2.token}")).email == EMAIL
        assert (await auth.current_user(f"Bearer {s.token}")).email == EMAIL
        # Tokens are stored hashed; the raw token never appears in the table.
        async with db.pool().acquire() as c:
            assert await c.fetchval("select count(*) from sessions where token_hash in ($1, $2)", s.token, s2.token) == 0
            assert await c.fetchval("select count(*) from sessions where user_id=$1", uuid.UUID(s.user.id)) == 2

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
        with pytest.raises(HTTPException) as e:                        # the right password is refused while locked
            await auth.login(EMAIL, PASSWORD)
        assert e.value.status_code == 429

        # Unlock (simulate time passing). Logout ends this session only; the other device is untouched.
        async with db.pool().acquire() as c:
            await c.execute("update users set locked_until=null where email=$1", EMAIL)
        s3 = await auth.login(EMAIL, PASSWORD)
        await auth.logout(s3.user.id, s3.token)
        with pytest.raises(HTTPException) as e:
            await auth.current_user(f"Bearer {s3.token}")
        assert e.value.status_code == 401
        assert (await auth.current_user(f"Bearer {s2.token}")).email == EMAIL   # other device still in

        # "Log out everywhere" ends every session of the account.
        s4 = await auth.login(EMAIL, PASSWORD)
        await auth.logout(s4.user.id, s4.token, everywhere=True)
        for dead in (s.token, s2.token, s4.token):
            with pytest.raises(HTTPException):
                await auth.current_user(f"Bearer {dead}")

        # Expiry: a session issued too long ago is refused and swept on the next sign-in.
        s5 = await auth.login(EMAIL, PASSWORD)
        async with db.pool().acquire() as c:
            await c.execute("update sessions set issued_at = now() - interval '31 days' where user_id=$1", uuid.UUID(s5.user.id))
        with pytest.raises(HTTPException) as e:
            await auth.current_user(f"Bearer {s5.token}")
        assert e.value.status_code == 401 and "expired" in e.value.detail
        await auth.login(EMAIL, PASSWORD)
        async with db.pool().acquire() as c:
            assert await c.fetchval("select count(*) from sessions where user_id=$1", uuid.UUID(s5.user.id)) == 1
    finally:
        async with db.pool().acquire() as c:
            await c.execute("delete from users where email=$1", EMAIL)
        await db.disconnect()


def test_account_lifecycle():
    _run(_flow())
