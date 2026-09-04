"""Watchlist + change-since-last-seen + the meaningfulness signal.

Reads serve quotes PERSISTED by the shared poller (fan-in: one upstream poll per symbol, many readers).
For each watched symbol with a snapshot AND real baselines we compute the live signal via app.scoring —
the SAME feature definitions the backtest validated — and attach reasons, a descriptive unusualness
rank, the numbers behind it (explainability), and the one learned tag (activity outlook).

Integrity notes:
  * mark_seen's monotonic watermark: the snapshot only ever advances forward, so a stale or concurrent
    "mark seen" (e.g. a second device) can never move a user's baseline backward.
  * We never hold a pooled DB connection across an upstream network fetch.
  * The model never gates visibility: every moved symbol still renders; flags come from thresholds.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import asyncpg

from . import baselines, db, quotes, scoring
from .baselines import INDEX_SYMBOL, Baselines
from .models import (Activity, Change, ChangeRow, Explain, Provenance, Signal, Snapshot, WatchRow)
from .providers import Quote, get_provider
from .providers.replay import ReplayProvider
from .quotes import LatestQuote
from .symbols import normalize

log = logging.getLogger("services")


def compute_change(seen_price: float, current_price: float) -> Change:
    abs_delta = round(current_price - seen_price, 4)
    pct = round((current_price - seen_price) / seen_price * 100, 4) if seen_price else 0.0
    direction = "up" if abs_delta > 0 else "down" if abs_delta < 0 else "flat"
    return Change(abs=abs_delta, pct=pct, direction=direction)


def _provenance(lq: LatestQuote | None, now: datetime) -> Provenance:
    if lq is None:
        return Provenance(source="none", is_simulated=False, event_time=now, age_seconds=0.0, freshness="no_data")
    return Provenance(
        source=lq.source, is_simulated=lq.source == "replay", event_time=lq.event_time,
        age_seconds=round(lq.age_seconds(now), 1), freshness=lq.freshness(now),
    )


# --------------------------------------------------------------------------- replay <- real baselines

def _sync_replay_profile(b: Baselines) -> None:
    """Anchor the simulator to the stock's REAL last close and size its events in the REAL sigma."""
    p = get_provider()
    if isinstance(p, ReplayProvider):
        p.set_profile(b.symbol, anchor=b.last_close, sigma_daily=b.ret_stdev_daily, avg_vol=b.avg_volume_20d)


async def sync_replay_profiles() -> None:
    """On startup: give the simulator real anchors/sigmas for everything watched (+ the index)."""
    async with db.pool().acquire() as conn:
        syms = [r["symbol"] for r in await conn.fetch("select distinct symbol from watchlist_items")]
        bs = await baselines.get_baselines(conn, syms + [INDEX_SYMBOL])
    for b in bs.values():
        _sync_replay_profile(b)


# --------------------------------------------------------------------------- quotes bootstrap

async def _ensure_quote(symbol: str) -> None:
    """Guarantee a FRESH quote after add/seen without waiting a poll cycle — reusing an existing fresh one
    (fan-in) and NEVER fetching while holding a pooled connection."""
    now = datetime.now(timezone.utc)
    async with db.pool().acquire() as conn:
        lq = (await quotes.latest_quotes(conn, [symbol])).get(symbol)
    if lq is not None and lq.freshness(now) == "fresh":
        return
    try:
        q: Quote = await get_provider().get_quote(symbol)  # outside any held connection
    except Exception:  # noqa: BLE001 — best-effort; the poller fills in within a cycle
        log.warning("bootstrap fetch failed for %s; poller will populate", symbol)
        return
    async with db.pool().acquire() as conn:
        prev = await quotes.latest_quotes(conn, [symbol])
        await quotes.record_quote(conn, q, prev.get(symbol))


# --------------------------------------------------------------------------- watchlist CRUD

async def add_symbol(user_id: str, raw_symbol: str) -> str:
    symbol = normalize(raw_symbol)
    async with db.pool().acquire() as conn:
        await conn.execute(
            "insert into watchlist_items (user_id, symbol) values ($1, $2) on conflict (user_id, symbol) do nothing",
            user_id, symbol,
        )
    # Real baselines first (cached; Yahoo down -> None and scoring degrades to 'no signal'), so the
    # simulator is anchored to real levels BEFORE the bootstrap quote is generated.
    b = await baselines.ensure_baselines(symbol)
    if b is not None:
        _sync_replay_profile(b)
    await _ensure_quote(symbol)
    await _ensure_quote(INDEX_SYMBOL)
    return symbol


async def remove_symbol(user_id: str, symbol: str) -> None:
    symbol = normalize(symbol)
    async with db.pool().acquire() as conn:
        await conn.execute("delete from watchlist_items where user_id=$1 and symbol=$2", user_id, symbol)
        await conn.execute("delete from read_state where user_id=$1 and symbol=$2", user_id, symbol)


async def _symbols_for(conn: asyncpg.Connection, user_id: str) -> list[str]:
    rows = await conn.fetch("select symbol from watchlist_items where user_id=$1 order by created_at", user_id)
    return [r["symbol"] for r in rows]


async def _read_state(conn: asyncpg.Connection, user_id: str) -> dict[str, dict]:
    rows = await conn.fetch("select symbol, snapshot_json from read_state where user_id=$1", user_id)
    return {r["symbol"]: json.loads(r["snapshot_json"]) for r in rows}


# --------------------------------------------------------------------------- the signal

def _build_signal(lq: LatestQuote, snap: dict, b: Baselines, idx_now: float | None) -> Signal | None:
    seen_time = datetime.fromisoformat(snap["event_time"])
    elapsed = max(0.0, (lq.event_time - seen_time).total_seconds())
    f = scoring.live_features(
        price_now=lq.price, price_seen=snap["price"],
        idx_now=idx_now, idx_seen=snap.get("index_price"),
        elapsed_seconds=elapsed,
        sigma_daily=b.ret_stdev_daily, beta=b.beta, avg_vol20=b.avg_volume_20d, vol_today=float(lq.volume),
        hi52=b.week52_high, lo52=b.week52_low,
    )
    if f is None:
        return None
    reasons = scoring.flags_and_reasons(f)
    out = scoring.activity_outlook(f)
    return Signal(
        reasons=reasons,
        is_meaningful=bool(reasons),
        unusualness=round(scoring.unusualness(f), 3),
        explain=Explain(
            sigma_move=round(f.abs_resid_z, 2), move_pct=round(f.move_pct * 100, 3),
            market_adjusted_pct=round(f.resid_pct * 100, 3), vol_ratio=round(f.vol_ratio, 2),
            crossed=f.crossed, sigma_daily_pct=round(b.ret_stdev_daily * 100, 2),
            beta=round(b.beta, 2) if b.beta is not None else None, elapsed_seconds=round(elapsed, 0),
        ),
        activity=Activity(probability=round(out[0], 3), version=out[1]) if out else None,
    )


async def list_watchlist(user_id: str) -> list[WatchRow]:
    now = datetime.now(timezone.utc)
    async with db.pool().acquire() as conn:
        symbols = await _symbols_for(conn, user_id)
        if not symbols:
            return []
        latest = await quotes.latest_quotes(conn, symbols + [INDEX_SYMBOL])
        seen = await _read_state(conn, user_id)
        bases = await baselines.get_baselines(conn, symbols)
    idx_now = latest[INDEX_SYMBOL].price if INDEX_SYMBOL in latest else None

    out: list[WatchRow] = []
    for sym in symbols:
        lq = latest.get(sym)
        row = WatchRow(symbol=sym, price=lq.price if lq else None, provenance=_provenance(lq, now), has_baseline=False)
        if sym in seen and lq is not None:
            snap = seen[sym]
            row.has_baseline = True
            row.last_seen = Snapshot(price=snap["price"], event_time=snap["event_time"])
            row.change_since_seen = compute_change(snap["price"], lq.price)
            if sym in bases:
                row.signal = _build_signal(lq, snap, bases[sym], idx_now)
        out.append(row)
    return out


# --------------------------------------------------------------------------- mark seen (monotonic)

async def _snapshot_upsert(conn: asyncpg.Connection, user_id: str, symbol: str, lq: LatestQuote,
                           idx_price: float | None) -> None:
    snapshot = json.dumps({"price": lq.price, "event_time": lq.event_time.isoformat(),
                           "source": lq.source, "index_price": idx_price})
    await conn.execute(
        """
        insert into read_state (user_id, symbol, watermark_event_time, snapshot_json, seen_at)
        values ($1, $2, $3, $4::jsonb, now())
        on conflict (user_id, symbol) do update
          set watermark_event_time = excluded.watermark_event_time,
              snapshot_json        = excluded.snapshot_json,
              seen_at              = now()
          where excluded.watermark_event_time >= read_state.watermark_event_time
        """,
        user_id, symbol, lq.event_time, snapshot,
    )


async def force_snapshot(conn: asyncpg.Connection, user_id: str, symbol: str, price: float,
                         event_time: datetime, idx_price: float | None) -> None:
    """DEV/DEMO ONLY: write a snapshot WITHOUT the monotonic guard (it moves the baseline backward on
    purpose, to re-create 'as of N minutes ago'). Never used by the normal mark-seen path."""
    snapshot = json.dumps({"price": price, "event_time": event_time.isoformat(),
                           "source": "replay", "index_price": idx_price})
    await conn.execute(
        """
        insert into read_state (user_id, symbol, watermark_event_time, snapshot_json, seen_at)
        values ($1, $2, $3, $4::jsonb, now())
        on conflict (user_id, symbol) do update
          set watermark_event_time = excluded.watermark_event_time,
              snapshot_json        = excluded.snapshot_json, seen_at = now()
        """,
        user_id, symbol, event_time, snapshot,
    )


async def mark_seen(user_id: str, symbol: str) -> None:
    """Freeze the quote the user is currently seeing (and the index level) as their baseline."""
    symbol = normalize(symbol)
    await _ensure_quote(symbol)
    async with db.pool().acquire() as conn:
        latest = await quotes.latest_quotes(conn, [symbol, INDEX_SYMBOL])
        if symbol in latest:
            idx = latest[INDEX_SYMBOL].price if INDEX_SYMBOL in latest else None
            await _snapshot_upsert(conn, user_id, symbol, latest[symbol], idx)


async def mark_all_seen(user_id: str) -> None:
    async with db.pool().acquire() as conn:
        async with conn.transaction():  # all-or-nothing
            symbols = await _symbols_for(conn, user_id)
            latest = await quotes.latest_quotes(conn, symbols + [INDEX_SYMBOL])
            idx = latest[INDEX_SYMBOL].price if INDEX_SYMBOL in latest else None
            for sym in symbols:
                if sym in latest:
                    await _snapshot_upsert(conn, user_id, sym, latest[sym], idx)


# --------------------------------------------------------------------------- the digest

def _changes_from_rows(rows: list[WatchRow]) -> list[ChangeRow]:
    """'While you were away', ranked by descriptive unusualness. Every moved symbol is included
    (rerank-never-suppress); is_meaningful marks the ones where a reason actually fired."""
    moved = [r for r in rows if r.has_baseline and r.change_since_seen and r.price is not None
             and r.change_since_seen.direction != "flat"]

    def key(r: WatchRow):
        if r.signal:
            return (0, -r.signal.unusualness)
        return (1, -abs(r.change_since_seen.pct))  # type: ignore[union-attr]
    moved.sort(key=key)

    out: list[ChangeRow] = []
    for r in moved:
        ch, snap = r.change_since_seen, r.last_seen
        assert ch is not None and snap is not None and r.price is not None
        sig = r.signal or Signal(
            reasons=[], is_meaningful=False, unusualness=0.0,
            explain=Explain(sigma_move=0.0, move_pct=ch.pct, market_adjusted_pct=ch.pct, vol_ratio=1.0,
                            crossed=None, sigma_daily_pct=0.0, beta=None, elapsed_seconds=0.0),
        )
        headline = " · ".join(sig.reasons) if sig.reasons else f"{ch.pct:+.2f}% since you last looked — nothing unusual"
        out.append(ChangeRow(symbol=r.symbol, price=r.price, provenance=r.provenance, last_seen=snap,
                             change_since_seen=ch, signal=sig, headline=headline))
    return out


async def get_state(user_id: str) -> tuple[list[WatchRow], list[ChangeRow]]:
    """Watchlist + ranked changes in ONE pass — the endpoint the client polls."""
    rows = await list_watchlist(user_id)
    return rows, _changes_from_rows(rows)


async def get_changes(user_id: str) -> list[ChangeRow]:
    return _changes_from_rows(await list_watchlist(user_id))
