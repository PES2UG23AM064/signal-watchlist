"""asyncpg connection pool + tiny migration runner.

We use asyncpg with explicit SQL (no ORM) on purpose: every query is visible and defensible,
and the integrity-critical logic (monotonic watermark, stale-write rejection in later milestones)
reads clearly as SQL inside an explicit transaction.
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
        _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)


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
