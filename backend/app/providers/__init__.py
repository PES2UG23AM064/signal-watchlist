"""Market data providers behind one interface (Replay is the backbone, Yahoo is live enrichment)."""
from __future__ import annotations

from ..config import settings
from .base import MarketDataProvider, Quote
from .replay import ReplayProvider

_provider: MarketDataProvider | None = None


def get_provider() -> MarketDataProvider:
    """Process-wide provider chosen by config. Kept behind the interface so swapping
    Replay <-> Yahoo (or adding fallback) is a one-line change here."""
    global _provider
    if _provider is None:
        if settings.market_provider == "replay":
            _provider = ReplayProvider(seed=settings.replay_seed)
        else:
            raise RuntimeError(f"Unknown MARKET_PROVIDER: {settings.market_provider}")
    return _provider


__all__ = ["MarketDataProvider", "Quote", "ReplayProvider", "get_provider"]
