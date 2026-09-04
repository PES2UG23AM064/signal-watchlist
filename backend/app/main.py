"""FastAPI app: lifespan wires the DB pool + migrations; routers expose the M1 spine.

The ingestion poller (M3) will attach here as an in-process asyncio task in this same lifespan,
so the whole system deploys as one Render container.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import settings
from .providers import get_provider
from .routes import auth, changes, watchlist


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await db.run_migrations()
    yield
    await db.disconnect()


app = FastAPI(title="Signal Watchlist", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(watchlist.router)
app.include_router(changes.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "provider": get_provider().name}
