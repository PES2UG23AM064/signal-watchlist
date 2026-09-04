"""Per-symbol baselines computed from REAL daily candles — the honest denominators for scoring.

  ret_stdev_daily : stdev of daily simple returns  -> the z-score denominator ("how big is this move
                    relative to how much this stock normally moves").
  avg_volume_20d  : mean of the last 20 daily volumes -> volume-anomaly denominator.
  week52_high/low : level-crossing references.
  beta            : slope of the stock's daily returns regressed on ^NSEI's -> lets index-relative
                    strength subtract the MARKET-driven part of a move (beta * index move), leaving the
                    idiosyncratic residual. Plain OLS via numpy — interpretable, one line.

Candles are cached in Postgres so scoring keeps working when Yahoo is down; refreshed when stale.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import asyncpg
import numpy as np

from . import db
from .providers.yahoo import Candle, YahooError, YahooProvider

log = logging.getLogger("baselines")

INDEX_SYMBOL = "^NSEI"
REFRESH_AFTER_HOURS = 24
MIN_BARS = 30


@dataclass(frozen=True)
class Baselines:
    symbol: str
    last_close: float
    ret_stdev_daily: float
    avg_volume_20d: float
    week52_high: float
    week52_low: float
    beta: float | None
    n_candles: int


def _returns(closes: np.ndarray) -> np.ndarray:
    return closes[1:] / closes[:-1] - 1.0


def compute(symbol: str, candles: list[Candle], index_candles: list[Candle] | None) -> Baselines:
    """Pure function over candles — unit-testable, no I/O."""
    if len(candles) < MIN_BARS:
        raise ValueError(f"need >= {MIN_BARS} candles for {symbol}, got {len(candles)}")
    closes = np.array([c.close for c in candles], dtype=float)
    vols = np.array([c.volume for c in candles], dtype=float)
    rets = _returns(closes)

    beta: float | None = None
    if index_candles:
        # Align stock and index by calendar day (holiday calendars differ slightly), then OLS slope.
        idx_by_day = {c.day: c.close for c in index_candles}
        pairs = [(c.close, idx_by_day[c.day]) for c in candles if c.day in idx_by_day]
        if len(pairs) >= MIN_BARS:
            s = np.array([p[0] for p in pairs]); m = np.array([p[1] for p in pairs])
            rs, rm = _returns(s), _returns(m)
            var_m = float(np.var(rm, ddof=1))
            if var_m > 0:
                beta = float(np.cov(rs, rm, ddof=1)[0, 1] / var_m)

    highs = [c.high for c in candles if c.high is not None] or list(closes)
    lows = [c.low for c in candles if c.low is not None] or list(closes)
    return Baselines(
        symbol=symbol,
        last_close=float(closes[-1]),
        ret_stdev_daily=float(np.std(rets, ddof=1)),
        avg_volume_20d=float(np.mean(vols[-20:])),
        week52_high=float(max(highs)),
        week52_low=float(min(lows)),
        beta=beta,
        n_candles=len(candles),
    )


async def _store_candles(conn: asyncpg.Connection, symbol: str, candles: list[Candle]) -> None:
    await conn.executemany(
        """
        insert into daily_candles (symbol, day, open, high, low, close, volume)
        values ($1, $2, $3, $4, $5, $6, $7)
        on conflict (symbol, day) do update
          set open=excluded.open, high=excluded.high, low=excluded.low,
              close=excluded.close, volume=excluded.volume
        """,
        [(symbol, c.day, c.open, c.high, c.low, c.close, c.volume) for c in candles],
    )


async def _store_baselines(conn: asyncpg.Connection, b: Baselines) -> None:
    await conn.execute(
        """
        insert into symbol_baselines
          (symbol, last_close, ret_stdev_daily, avg_volume_20d, week52_high, week52_low, beta, n_candles, computed_at)
        values ($1, $2, $3, $4, $5, $6, $7, $8, now())
        on conflict (symbol) do update set
          last_close=excluded.last_close, ret_stdev_daily=excluded.ret_stdev_daily,
          avg_volume_20d=excluded.avg_volume_20d, week52_high=excluded.week52_high,
          week52_low=excluded.week52_low, beta=excluded.beta, n_candles=excluded.n_candles,
          computed_at=now()
        """,
        b.symbol, b.last_close, b.ret_stdev_daily, b.avg_volume_20d,
        b.week52_high, b.week52_low, b.beta, b.n_candles,
    )


async def get_baselines(conn: asyncpg.Connection, symbols: list[str]) -> dict[str, Baselines]:
    if not symbols:
        return {}
    rows = await conn.fetch("select * from symbol_baselines where symbol = any($1::text[])", symbols)
    return {
        r["symbol"]: Baselines(
            symbol=r["symbol"], last_close=r["last_close"], ret_stdev_daily=r["ret_stdev_daily"],
            avg_volume_20d=r["avg_volume_20d"], week52_high=r["week52_high"], week52_low=r["week52_low"],
            beta=r["beta"], n_candles=r["n_candles"],
        )
        for r in rows
    }


async def load_candles(conn: asyncpg.Connection, symbol: str) -> list[Candle]:
    """Cached real history, oldest -> newest (also the backtest's input)."""
    rows = await conn.fetch(
        "select day, open, high, low, close, volume from daily_candles where symbol=$1 order by day", symbol
    )
    return [Candle(r["day"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows]


async def _is_fresh(conn: asyncpg.Connection, symbol: str) -> bool:
    age_h = await conn.fetchval(
        "select extract(epoch from (now() - computed_at))/3600 from symbol_baselines where symbol=$1", symbol
    )
    return age_h is not None and age_h < REFRESH_AFTER_HOURS


async def ensure_baselines(symbol: str, force: bool = False) -> Baselines | None:
    """Backfill (or refresh stale) baselines for one symbol. Network happens OUTSIDE any held DB
    connection. Returns None (and logs) if Yahoo is unavailable — callers degrade gracefully.
    The index (^NSEI) is cached with the same freshness rule, so it is fetched at most once per
    refresh window, not once per symbol added."""
    async with db.pool().acquire() as conn:
        if not force and await _is_fresh(conn, symbol):
            return (await get_baselines(conn, [symbol])).get(symbol)

    yahoo = YahooProvider()
    try:
        candles = await yahoo.get_history(symbol)
    except YahooError as e:
        log.warning("baseline backfill unavailable for %s: %s", symbol, e)
        return None

    index_candles: list[Candle] | None = None
    if symbol != INDEX_SYMBOL:
        await ensure_baselines(INDEX_SYMBOL)  # cached; only hits Yahoo if the index cache is stale
        async with db.pool().acquire() as conn:
            index_candles = await load_candles(conn, INDEX_SYMBOL) or None

    b = compute(symbol, candles, index_candles)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await _store_candles(conn, symbol, candles)
            await _store_baselines(conn, b)
    log.info("baselines for %s: stdev=%.4f avgvol=%.0f beta=%s n=%d",
             symbol, b.ret_stdev_daily, b.avg_volume_20d, f"{b.beta:.2f}" if b.beta is not None else None, b.n_candles)
    return b
