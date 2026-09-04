"""asyncpg connection pool and a tiny migration runner.

Explicit SQL, no ORM: the integrity-critical logic (monotonic watermark, stale-write rejection)
reads as SQL inside an explicit transaction.
"""
from __future__ import annotations

import pathlib

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


async def connect() -> None:
    global _pool
    if _pool is None:
        # Supabase's session pooler allows 15 clients per project. Each instance holds max_size + 1
        # (the poller's lock session), so 4 + 1 lets the deployed API, a local dev and CI coexist.
        _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=4)


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call connect() first.")
    return _pool


async def run_migrations() -> None:
    """Apply every .sql file in migrations/ in filename order. Files are idempotent."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    async with pool().acquire() as conn:
        for f in files:
            await conn.execute(f.read_text(encoding="utf-8"))
