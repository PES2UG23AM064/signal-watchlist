"""Market data providers behind one interface.

MARKET_PROVIDER selects the process-wide provider:
  replay     (default) deterministic simulator — the reproducible demo backbone, labeled "simulated".
  yahoo      live Yahoo quotes only (no fallback; for experiments).
  composite  live Yahoo when NSE is open, with a circuit breaker and honest fallback to Replay on
             failure / outside market hours — the "how it runs against a real feed" configuration.
"""
from __future__ import annotations

from ..config import settings
from .base import MarketDataProvider, Quote
from .composite import CompositeProvider
from .replay import ReplayProvider
from .yahoo import YahooProvider

_provider: MarketDataProvider | None = None


def get_provider() -> MarketDataProvider:
    global _provider
    if _provider is None:
        mode = settings.market_provider
        if mode == "replay":
            _provider = ReplayProvider(seed=settings.replay_seed)
        elif mode == "yahoo":
            _provider = YahooProvider()
        elif mode == "composite":
            secondary = None
            if settings.twelvedata_api_key:
                from .twelvedata import TwelveDataProvider
                secondary = TwelveDataProvider(settings.twelvedata_api_key)
            _provider = CompositeProvider(primary=YahooProvider(), fallback=ReplayProvider(seed=settings.replay_seed),
                                          secondary=secondary)
        else:
            raise RuntimeError(f"Unknown MARKET_PROVIDER: {mode}")
    return _provider


def replay_instance() -> ReplayProvider | None:
    """The Replay simulator in play, if any (directly, or as a composite's fallback) — so real baselines
    can anchor it and the demo rewind can use its deterministic history."""
    p = get_provider()
    if isinstance(p, ReplayProvider):
        return p
    fb = getattr(p, "fallback", None)
    return fb if isinstance(fb, ReplayProvider) else None


def provider_status() -> dict:
    p = get_provider()
    if isinstance(p, CompositeProvider):
        return {"mode": "composite", **p.status()}
    return {"mode": p.name, "serving_live": p.name == "yahoo"}


__all__ = ["MarketDataProvider", "Quote", "ReplayProvider", "YahooProvider", "CompositeProvider",
           "get_provider", "replay_instance", "provider_status"]
