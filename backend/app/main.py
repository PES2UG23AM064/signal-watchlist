"""FastAPI app: lifespan wires the DB pool + migrations + the in-process poller; routers expose the API.

The whole system (API + ingestion poller) deploys as ONE Render container: the poller is an asyncio
task started here in lifespan, not a separate worker service.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db, poller, services
from .config import settings
from .market import market_status
from .models import MarketStatusModel
from .providers import get_provider
from .routes import auth, dev, events, model, state, status, watchlist
from .symbols import InvalidSymbol

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await db.run_migrations()
    await services.sync_replay_profiles()  # anchor the simulator to REAL closes/sigmas before polling

    stop = asyncio.Event()
    task: asyncio.Task | None = None
    if settings.run_poller:
        task = asyncio.create_task(poller.run(stop))

    yield

    stop.set()
    if task is not None:
        await task
    await db.disconnect()


app = FastAPI(title="Signal Watchlist", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex,  # this project's own *.onrender.com deploys
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(InvalidSymbol)
async def _invalid_symbol(_: Request, exc: InvalidSymbol) -> JSONResponse:
    """A symbol that can't be a ticker is the caller's mistake (400), never a 500 — whichever route it
    arrives through (body or path)."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(auth.router)
app.include_router(watchlist.router)
app.include_router(state.router)   # the ONE digest surface: watchlist + ranked changes + cohorts
app.include_router(model.router)
app.include_router(dev.router)
app.include_router(status.router)
app.include_router(events.router)


@app.get("/market", response_model=MarketStatusModel, tags=["meta"])
async def market() -> MarketStatusModel:
    s = market_status()
    return MarketStatusModel(is_open=s.is_open, label=s.label, detail=s.detail)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "provider": get_provider().name}
