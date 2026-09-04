"""Watchlist + change-since-last-seen + the meaningfulness signal.

Reads serve quotes PERSISTED by the shared poller (fan-in: one upstream poll per symbol, many readers).
For each watched symbol with a snapshot AND real baselines we compute the live signal via app.scoring —
the SAME feature definitions the backtest validated — and attach reasons, a descriptive unusualness
rank, the numbers behind it (explainability), the one learned tag (activity outlook), co-movement
cohort context, and — if the user told us what they hold — the rupee impact.

Integrity notes:
  * mark_seen's monotonic watermark: the snapshot only ever advances forward, so a stale or concurrent
    "mark seen" (e.g. a second device) can never move a user's baseline backward.
  * We never hold a pooled DB connection across an upstream network fetch.
  * The model never gates visibility: every moved symbol still renders; flags come from thresholds.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone

import asyncpg
import numpy as np

from . import baselines, cohorts, db, poller, quotes, scoring
from .baselines import INDEX_SYMBOL, Baselines
from .config import settings
from .models import (Activity, Change, ChangeRow, CohortInfo, Explain, Provenance, Signal, Snapshot, WatchRow)
from .providers import Quote, get_provider, replay_instance
from .quotes import LatestQuote
from .symbols import normalize

log = logging.getLogger("services")

QUARANTINE_WINDOW = "5 minutes"


def compute_change(seen_price: float, current_price: float) -> Change:
    abs_delta = round(current_price - seen_price, 4)
    pct = round((current_price - seen_price) / seen_price * 100, 4) if seen_price else 0.0
    direction = "up" if abs_delta > 0 else "down" if abs_delta < 0 else "flat"
    return Change(abs=abs_delta, pct=pct, direction=direction)


def _provenance(lq: LatestQuote | None, now: datetime, quarantined: int = 0) -> Provenance:
    if lq is None:
        return Provenance(source="none", is_simulated=False, event_time=now, age_seconds=0.0,
                          freshness="no_data", quarantined_recent=quarantined)
    return Provenance(
        source=lq.source, is_simulated=lq.source == "replay", event_time=lq.event_time,
        age_seconds=round(lq.age_seconds(now), 1), freshness=lq.freshness(now), quarantined_recent=quarantined,
    )


# --------------------------------------------------------------------------- replay <- real baselines

def _sync_replay_profile(b: Baselines) -> None:
    """Anchor the simulator (direct, or a composite's fallback) to the stock's REAL last close and size its
    events in the REAL sigma."""
    rp = replay_instance()
    if rp is not None:
        rp.set_profile(b.symbol, anchor=b.last_close, sigma_daily=b.ret_stdev_daily, avg_vol=b.avg_volume_20d)


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
    (fan-in) and NEVER fetching while holding a pooled connection. A symbol whose upstream is (simulated)
    down is left alone so its data honestly ages."""
    if poller.is_paused(symbol):
        return
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


async def set_quantity(user_id: str, symbol: str, quantity: float | None) -> None:
    symbol = normalize(symbol)
    if quantity is not None and quantity < 0:
        quantity = None
    async with db.pool().acquire() as conn:
        await conn.execute("update watchlist_items set quantity=$3 where user_id=$1 and symbol=$2",
                           user_id, symbol, quantity)


async def _symbols_for(conn: asyncpg.Connection, user_id: str) -> list[str]:
    rows = await conn.fetch("select symbol from watchlist_items where user_id=$1 order by created_at", user_id)
    return [r["symbol"] for r in rows]


async def _holdings(conn: asyncpg.Connection, user_id: str) -> dict[str, float | None]:
    rows = await conn.fetch("select symbol, quantity from watchlist_items where user_id=$1 order by created_at", user_id)
    return {r["symbol"]: r["quantity"] for r in rows}


async def _read_state(conn: asyncpg.Connection, user_id: str) -> dict[str, dict]:
    rows = await conn.fetch("select symbol, snapshot_json from read_state where user_id=$1", user_id)
    return {r["symbol"]: json.loads(r["snapshot_json"]) for r in rows}


async def _quarantined_recent(conn: asyncpg.Connection, symbols: list[str]) -> dict[str, int]:
    """Bad ticks rejected recently, per symbol — makes the quarantine VISIBLE rather than silent."""
    if not symbols:
        return {}
    rows = await conn.fetch(
        f"select symbol, count(*) as n from quotes where is_suspect and symbol = any($1::text[]) "
        f"and event_time > now() - interval '{QUARANTINE_WINDOW}' group by symbol", symbols)
    return {r["symbol"]: int(r["n"]) for r in rows}


# --------------------------------------------------------------------------- the signal

PEAK_FLAG_SIGMA = 2.0   # an excursion this large (in sigma) is reported even if the endpoint came back
PEAK_RETRACE_MAX_SIGMA = 1.0  # ...and "retraced" means the endpoint ended within this much


async def _peaks_since(conn: asyncpg.Connection, seen: dict[str, dict]) -> dict[str, tuple[float, float]]:
    """(max, min) served price per symbol since the user's snapshot time — from the quote ring, capped by
    the retention window. Uses the (symbol, event_time) index; one small query per watched symbol."""
    out: dict[str, tuple[float, float]] = {}
    for sym, snap in seen.items():
        since = datetime.fromisoformat(snap["event_time"])
        r = await conn.fetchrow(
            "select max(price) as hi, min(price) as lo from quotes "
            "where symbol=$1 and not is_suspect and event_time > $2", sym, since)
        if r and r["hi"] is not None:
            out[sym] = (float(r["hi"]), float(r["lo"]))
    return out


def _build_signal(lq: LatestQuote, snap: dict, b: Baselines, idx_now: float | None,
                  peaks: tuple[float, float] | None = None) -> Signal | None:
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

    # Path since you looked: the endpoint diff misses "+3% then back to flat". Report the excursion, and
    # flag it when the path was a >=2-sigma event that the endpoint (<1 sigma) would have hidden.
    peak_pct = trough_pct = None
    path_note = None
    path_bonus = 0.0
    if peaks and snap["price"] > 0:
        hi, lo = peaks
        peak_pct = round((max(hi, lq.price, snap["price"]) / snap["price"] - 1) * 100, 3)
        trough_pct = round((min(lo, lq.price, snap["price"]) / snap["price"] - 1) * 100, 3)
        excursion = max(abs(peak_pct), abs(trough_pct)) / 100.0
        sigma_eff = f.sigma_used
        if sigma_eff > 0 and excursion / sigma_eff >= PEAK_FLAG_SIGMA and f.abs_resid_z < PEAK_RETRACE_MAX_SIGMA:
            direction = "spiked" if abs(peak_pct) >= abs(trough_pct) else "dropped"
            ext = peak_pct if direction == "spiked" else trough_pct
            path_note = f"{direction} {ext:+.1f}% then retraced ({excursion / sigma_eff:.1f}σ path move)"
            reasons.append(path_note)
            path_bonus = excursion / sigma_eff   # the path WAS the event; rank it like one

    out = scoring.activity_outlook(f)
    return Signal(
        reasons=reasons,
        is_meaningful=bool(reasons),
        unusualness=round(scoring.unusualness(f) + path_bonus, 3),
        explain=Explain(
            sigma_move=round(f.abs_resid_z, 2), move_pct=round(f.move_pct * 100, 3),
            market_adjusted_pct=round(f.resid_pct * 100, 3), vol_ratio=round(f.vol_ratio, 2),
            crossed=f.crossed, sigma_daily_pct=round(b.ret_stdev_daily * 100, 2),
            beta=round(b.beta, 2) if b.beta is not None else None, elapsed_seconds=round(elapsed, 0),
            peak_pct=peak_pct, trough_pct=trough_pct, path_note=path_note,
        ),
        activity=Activity(probability=round(out[0], 3), version=out[1]) if out else None,
    )


async def list_watchlist(user_id: str) -> list[WatchRow]:
    now = datetime.now(timezone.utc)
    async with db.pool().acquire() as conn:
        holdings = await _holdings(conn, user_id)
        symbols = list(holdings)
        if not symbols:
            return []
        latest = await quotes.latest_quotes(conn, symbols + [INDEX_SYMBOL])
        seen = await _read_state(conn, user_id)
        bases = await baselines.get_baselines(conn, symbols)
        quarantined = await _quarantined_recent(conn, symbols)
        peaks = await _peaks_since(conn, seen)
        snoozes = await _snoozes(conn, user_id)
        from .config import settings as _cfg  # dispute thresholds
        disputed = await quotes.disputes(conn, symbols, _cfg.dispute_threshold_pct, _cfg.dispute_window_seconds)
    idx_now = latest[INDEX_SYMBOL].price if INDEX_SYMBOL in latest else None

    out: list[WatchRow] = []
    for sym in symbols:
        lq = latest.get(sym)
        qty = holdings.get(sym)
        snoozed_until = snoozes.get(sym)
        row = WatchRow(symbol=sym, price=lq.price if lq else None,
                       provenance=_provenance(lq, now, quarantined.get(sym, 0)), has_baseline=False,
                       quantity=qty, exposure_inr=round(qty * lq.price, 2) if (qty and lq) else None,
                       snoozed_until=snoozed_until if (snoozed_until and snoozed_until > now) else None)
        if sym in disputed:
            row.provenance.disputed = True
            row.provenance.dispute = disputed[sym]
        if sym in seen and lq is not None:
            snap = seen[sym]
            row.has_baseline = True
            row.last_seen = Snapshot(price=snap["price"], event_time=snap["event_time"])
            row.change_since_seen = compute_change(snap["price"], lq.price)
            if qty:
                row.impact_inr = round(qty * (lq.price - snap["price"]), 2)
            if sym in bases:
                row.signal = _build_signal(lq, snap, bases[sym], idx_now, peaks.get(sym))
        out.append(row)
    return out


async def _snoozes(conn: asyncpg.Connection, user_id: str) -> dict[str, datetime]:
    rows = await conn.fetch(
        "select symbol, snoozed_until from watchlist_items where user_id=$1 and snoozed_until is not null", user_id)
    return {r["symbol"]: r["snoozed_until"] for r in rows}


async def snooze(user_id: str, symbol: str, minutes: int | None) -> None:
    """Hold a symbol out of 'needs your attention' until then (None clears). It stays listed — nothing hidden."""
    symbol = normalize(symbol)
    until = (datetime.now(timezone.utc) + __import__("datetime").timedelta(minutes=minutes)) if minutes else None
    async with db.pool().acquire() as conn:
        await conn.execute("update watchlist_items set snoozed_until=$3 where user_id=$1 and symbol=$2",
                           user_id, symbol, until)


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


# --------------------------------------------------------------------------- co-movement cohorts

_COHORT_CACHE: dict[frozenset, tuple[float, cohorts.CohortModel | None]] = {}
COHORT_TTL_S = 3600  # cohorts change when the watchlist changes (key) or daily as candles refresh


async def _cohort_model(conn: asyncpg.Connection, symbols: list[str]) -> cohorts.CohortModel | None:
    """The user's co-movement cohorts from cached REAL candles, memoized per watchlist-set for an hour."""
    key = frozenset(symbols)
    hit = _COHORT_CACHE.get(key)
    if hit and time.time() - hit[0] < COHORT_TTL_S:
        return hit[1]
    cands = {s: c for s in symbols if (c := await baselines.load_candles(conn, s))}
    model = cohorts.build(cands) if len(cands) >= 2 else None
    _COHORT_CACHE[key] = (time.time(), model)
    return model


def _cohort_infos(model: cohorts.CohortModel | None) -> list[CohortInfo]:
    if model is None:
        return []
    idx = {s: i for i, s in enumerate(model.symbols)}
    out = []
    for k, g in enumerate(model.cohorts):
        mean_corr = None
        if len(g) > 1 and all(s in idx for s in g) and model.corr.size:
            pairs = [model.corr[idx[a], idx[b]] for a in g for b in g if a < b]
            mean_corr = round(float(np.mean(pairs)), 2) if pairs else None
        out.append(CohortInfo(id=k, members=g, mean_corr=mean_corr))
    return out


# --------------------------------------------------------------------------- the digest

def attention_score(unusualness: float, impact_inr: float | None) -> float:
    """Transparent ranking score. Unusualness (sigma-equivalent units) gently amplified by rupees at
    stake: x1.3 at ~Rs1k impact, x2 at ~Rs10k, x3 at ~Rs1L. Holdings AMPLIFY an unusual move; they never
    drown one out (a big move on something you don't hold still ranks by its own unusualness)."""
    if impact_inr is None:
        return unusualness
    return unusualness * (1.0 + math.log10(1.0 + abs(impact_inr) / 1000.0))


def _changes_from_rows(rows: list[WatchRow], model: cohorts.CohortModel | None = None) -> list[ChangeRow]:
    """'While you were away'. Every moved symbol is included (rerank-never-suppress); is_meaningful marks
    the ones where a reason fired. With a cohort model: symbols moving ALONE vs their co-movement peers
    are promoted to the top; pack moves are tagged with their cohort so the client can collapse them.
    Grouping is presentation only — nothing is ever removed from this list."""
    moved = [r for r in rows if r.has_baseline and r.change_since_seen and r.price is not None
             and r.change_since_seen.direction != "flat"]

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
        if r.snoozed_until is not None:
            # Snoozed: still LISTED with its reasons (nothing hidden), but not counted as needing attention.
            sig = sig.model_copy(update={"is_meaningful": False})
        out.append(ChangeRow(symbol=r.symbol, price=r.price, provenance=r.provenance, last_seen=snap,
                             change_since_seen=ch, signal=sig, headline=headline,
                             quantity=r.quantity, impact_inr=r.impact_inr, snoozed_until=r.snoozed_until))

    if model is not None and out:
        moves = {c.symbol: c.change_since_seen.pct / 100.0 for c in out}
        elapsed = float(np.median([c.signal.explain.elapsed_seconds for c in out])) or scoring.MIN_ELAPSED_S
        res = cohorts.peer_residuals(model, moves, elapsed_seconds=elapsed,
                                     trading_day_s=scoring.TRADING_DAY_S, min_elapsed_s=scoring.MIN_ELAPSED_S)
        for c in out:
            c.cohort_id = model.cohort_of.get(c.symbol)
            pr = res.get(c.symbol)
            if pr is not None:
                z = float(pr[1])
                c.peer_residual_z = round(z, 2)
                c.moving_alone = bool(abs(z) >= cohorts.PEER_Z_FLAG)  # plain bool: numpy.bool_ won't serialize
                if c.moving_alone and not any(("peers" in r or "co-mover" in r) for r in c.signal.reasons):
                    present = [s for s in model.cohorts[c.cohort_id] if s in moves] if c.cohort_id is not None else []
                    if len(present) == 2:
                        # A PAIR that diverged: you can't say which one is "alone" — say what's true instead.
                        other = next(s for s in present if s != c.symbol).replace(".NS", "")
                        c.signal.reasons.append(f"diverging {abs(z):.1f}σ from {other}, which it usually moves with")
                    else:
                        c.signal.reasons.append(f"moving alone: {abs(z):.1f}σ vs its co-movement peers")
                    if c.snoozed_until is None:   # snoozed stays listed-not-counted, even if it diverges
                        c.signal.is_meaningful = True
                    c.headline = " · ".join(c.signal.reasons)

    def key(c: ChangeRow):
        # snoozed last; moving-alone first (biggest divergence first); then attention (unusualness x
        # rupees at stake); then raw size as a final tiebreak
        return (2 if c.snoozed_until is not None else (0 if c.moving_alone else 1),
                -(abs(c.peer_residual_z) if c.moving_alone and c.peer_residual_z is not None else 0.0),
                -attention_score(c.signal.unusualness, c.impact_inr), -abs(c.change_since_seen.pct))
    out.sort(key=key)
    return out


async def get_state(user_id: str) -> tuple[list[WatchRow], list[ChangeRow], list[CohortInfo]]:
    """Watchlist + ranked changes + cohorts in ONE pass — the endpoint the client polls."""
    rows = await list_watchlist(user_id)
    symbols = [r.symbol for r in rows]
    model = None
    if len(symbols) >= 2:
        async with db.pool().acquire() as conn:
            model = await _cohort_model(conn, symbols)
    return rows, _changes_from_rows(rows, model), _cohort_infos(model)


async def get_changes(user_id: str) -> list[ChangeRow]:
    return _changes_from_rows(await list_watchlist(user_id))
