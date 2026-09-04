"""Watchlist + change-since-last-seen + the meaningfulness signal.

Reads serve quotes PERSISTED by the shared poller (fan-in: one upstream poll per symbol, many readers).
For each watched symbol with a snapshot AND real baselines we compute the live signal via app.scoring —
the SAME feature definitions the backtest ran — and attach plain-English reasons, a descriptive
unusualness rank, the numbers behind it (explainability), co-movement cohort context, and — if the user
told us what they hold — the rupee impact.

Integrity notes:
  * mark_seen's monotonic watermark: the snapshot only ever advances forward, so a stale or concurrent
    "mark seen" (e.g. a second device) can never move a user's baseline backward.
  * We never hold a pooled DB connection across an upstream network fetch.
  * Nothing gates visibility: every moved symbol still renders; flags come from thresholds.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import UTC, datetime, timedelta

import asyncpg
import numpy as np

from . import baselines, cohorts, db, poller, quotes, scoring
from .baselines import INDEX_SYMBOL, Baselines
from .config import settings
from .models import (
    Change,
    ChangeRow,
    CohortInfo,
    Explain,
    Provenance,
    Signal,
    Snapshot,
    WatchRow,
)
from .providers import Quote, get_provider, replay_instance
from .providers.yahoo import SymbolNotFound
from .quotes import LatestQuote
from .symbols import normalize

log = logging.getLogger("services")

QUARANTINE_WINDOW_S = 300  # "bad ticks rejected recently" = last 5 minutes


FLAT_PCT = 0.01  # below one basis point since you looked, a symbol has not "moved" — a paisa tick is not news


def compute_change(seen_price: float, current_price: float) -> Change:
    abs_delta = round(current_price - seen_price, 4)
    pct = round((current_price - seen_price) / seen_price * 100, 4) if seen_price else 0.0
    direction = "flat" if abs(pct) < FLAT_PCT else "up" if abs_delta > 0 else "down"
    return Change(abs=abs_delta, pct=pct, direction=direction)


def _provenance(lq: LatestQuote | None, now: datetime, quarantined: int = 0) -> Provenance:
    if lq is None:
        return Provenance(source="none", is_simulated=False, event_time=now, age_seconds=0.0,
                          freshness="no_data", quarantined_recent=quarantined)
    return Provenance(
        source=lq.source, is_simulated=lq.source == "replay", event_time=lq.event_time,
        age_seconds=round(lq.age_seconds(now), 1), freshness=lq.freshness(now), quarantined_recent=quarantined,
    )


def _snap_time(snap: dict) -> datetime | None:
    """A snapshot we wrote ourselves — but a malformed row must degrade to 'no snapshot for this symbol',
    never 500 the whole digest."""
    try:
        return datetime.fromisoformat(snap["event_time"])
    except (KeyError, TypeError, ValueError):
        log.warning("malformed snapshot ignored: %r", snap)
        return None


# --------------------------------------------------------------------------- replay <- real baselines

def _sync_replay_profile(b: Baselines) -> None:
    """Anchor the simulator (direct, or a composite's fallback) to the stock's REAL last close and size its
    events in the REAL sigma."""
    rp = replay_instance()
    if rp is not None:
        rp.set_profile(b.symbol, anchor=b.last_close, sigma_daily=b.ret_stdev_daily, avg_vol=b.avg_volume_20d)


async def _sync_replay_groups(conn: asyncpg.Connection) -> None:
    """Tell the simulator which watched symbols the REAL candles say move together, so its 'sector days'
    hit the same packs the app's cohort cards are built from (nothing is invented: same model, same data)."""
    rp = replay_instance()
    if rp is None:
        return
    syms = [r["symbol"] for r in await conn.fetch("select distinct symbol from watchlist_items")]
    model = await _cohort_model(conn, syms) if len(syms) >= 2 else None
    rp.set_groups([g for g in model.cohorts if len(g) > 1] if model else [])


async def sync_replay_profiles() -> None:
    """On startup: give the simulator real anchors/sigmas for everything watched (+ the index), and its packs."""
    async with db.pool().acquire() as conn:
        syms = [r["symbol"] for r in await conn.fetch("select distinct symbol from watchlist_items")]
        bs = await baselines.get_baselines(conn, syms + [INDEX_SYMBOL])
        for b in bs.values():
            _sync_replay_profile(b)
        await _sync_replay_groups(conn)


# --------------------------------------------------------------------------- quotes bootstrap

async def _ensure_quote(symbol: str) -> None:
    """Guarantee a FRESH quote after add/seen without waiting a poll cycle — reusing an existing fresh one
    (fan-in) and NEVER fetching while holding a pooled connection. A symbol whose upstream is (simulated)
    down is left alone so its data honestly ages."""
    if poller.is_paused(symbol):
        return
    now = datetime.now(UTC)
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
        anchor = (await baselines.get_baselines(conn, [symbol])).get(symbol)
        await quotes.record_quote(conn, q, prev.get(symbol), anchor=anchor.last_close if anchor else None)


# --------------------------------------------------------------------------- watchlist CRUD

class UnknownSymbol(Exception):
    """The exchange has no such symbol; nothing was added."""


class SymbolUnavailable(Exception):
    """We could not get a price for this symbol right now (upstream down) and won't add a dead row."""


async def add_symbol(user_id: str, raw_symbol: str) -> str:
    symbol = normalize(raw_symbol)
    # Validate BEFORE inserting: a typo must not become a permanent "no data" row on the list.
    try:
        b = await baselines.ensure_baselines(symbol)
    except SymbolNotFound as e:
        raise UnknownSymbol(symbol) from e
    if b is None:
        rp = replay_instance()
        if rp is not None and not rp.can_quote(symbol):
            raise SymbolUnavailable(symbol)   # no real anchor and Yahoo is down: we won't invent a price
    async with db.pool().acquire() as conn:
        await conn.execute(
            "insert into watchlist_items (user_id, symbol) values ($1, $2) on conflict (user_id, symbol) do nothing",
            user_id, symbol,
        )
    if b is not None:
        _sync_replay_profile(b)
    # The symbol and the index need a fresh quote before the snapshot; they are independent fetches.
    await asyncio.gather(_ensure_quote(symbol), _ensure_quote(INDEX_SYMBOL))
    # Adding a symbol IS looking at it: the price on screen at this moment becomes the first baseline, so
    # "what changed since I last looked" works from the first return visit — no separate "Seen" needed.
    # Idempotent re-adds go through the monotonic guard, so they can never move an existing baseline back.
    async with db.pool().acquire() as conn:
        latest = await quotes.latest_quotes(conn, [symbol, INDEX_SYMBOL])
        if symbol in latest:
            idx = latest[INDEX_SYMBOL].price if INDEX_SYMBOL in latest else None
            await _snapshot_upsert(conn, user_id, symbol, latest[symbol], idx)
    if b is not None:
        # A new symbol may join (or form) a simulator pack. That re-clusters every watched symbol's candles —
        # the slowest thing in this path and nothing the caller is waiting on. It runs after we respond; a
        # failure there is logged, never surfaced as a failed add.
        _background(_resync_replay_groups(), "replay pack re-sync")
    return symbol


_BACKGROUND: set[asyncio.Task] = set()


def _background(coro, what: str) -> None:
    """Fire-and-forget with a kept reference (a bare create_task can be garbage-collected mid-flight)."""
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)

    def _done(t: asyncio.Task) -> None:
        _BACKGROUND.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.warning("%s failed: %r", what, t.exception())
    task.add_done_callback(_done)


async def _resync_replay_groups() -> None:
    async with db.pool().acquire() as conn:
        await _sync_replay_groups(conn)


async def remove_symbol(user_id: str, symbol: str) -> bool:
    """Remove a symbol and its snapshot. False if it wasn't on the list."""
    symbol = normalize(symbol)
    async with db.pool().acquire() as conn:
        status = await conn.execute("delete from watchlist_items where user_id=$1 and symbol=$2", user_id, symbol)
        await conn.execute("delete from read_state where user_id=$1 and symbol=$2", user_id, symbol)
    return _updated(status)


def _updated(status: str) -> bool:
    """asyncpg returns e.g. 'UPDATE 1' — False means the row wasn't the caller's (route -> 404)."""
    return not status.endswith(" 0")


async def set_quantity(user_id: str, symbol: str, quantity: float | None) -> bool:
    """Shares held (validated >= 0 at the API edge). Zero means "I don't hold it": same as clearing."""
    symbol = normalize(symbol)
    if quantity is not None and quantity <= 0:
        quantity = None
    async with db.pool().acquire() as conn:
        status = await conn.execute("update watchlist_items set quantity=$3 where user_id=$1 and symbol=$2",
                                    user_id, symbol, quantity)
    return _updated(status)


async def _symbols_for(conn: asyncpg.Connection, user_id: str) -> list[str]:
    rows = await conn.fetch("select symbol from watchlist_items where user_id=$1 order by created_at", user_id)
    return [r["symbol"] for r in rows]


async def _holdings(conn: asyncpg.Connection, user_id: str) -> dict[str, float | None]:
    rows = await conn.fetch("select symbol, quantity from watchlist_items where user_id=$1 order by created_at", user_id)
    return {r["symbol"]: r["quantity"] for r in rows}


async def _read_state(conn: asyncpg.Connection, user_id: str) -> dict[str, dict]:
    rows = await conn.fetch("select symbol, snapshot_json from read_state where user_id=$1", user_id)
    out = {}
    for r in rows:
        snap = json.loads(r["snapshot_json"])
        if _snap_time(snap) is not None and isinstance(snap.get("price"), int | float) and snap["price"] > 0:
            out[r["symbol"]] = snap
    return out


async def _quarantined_recent(conn: asyncpg.Connection, symbols: list[str]) -> dict[str, int]:
    """Bad ticks rejected recently, per symbol — makes the quarantine VISIBLE rather than silent."""
    if not symbols:
        return {}
    # Window on received_at (when WE received it), not event_time: a future-dated bad tick has an
    # event_time an hour ahead and would otherwise read as "recent" until the clock catches up.
    rows = await conn.fetch(
        "select symbol, count(*) as n from quotes where is_suspect and symbol = any($1::text[]) "
        "and received_at > now() - make_interval(secs => $2) group by symbol", symbols, float(QUARANTINE_WINDOW_S))
    return {r["symbol"]: int(r["n"]) for r in rows}


# --------------------------------------------------------------------------- the signal

PEAK_FLAG_SIGMA = 2.0         # an excursion this large (in sigma) is reported even if the endpoint came back
PEAK_RETRACE_MAX_SIGMA = 1.0  # ...and "retraced" means the endpoint ended within this much
PATH_BONUS_CAP = 3.0          # a retraced spike ranks like an event, but a wild wick can't dominate the digest


async def _peaks_since(conn: asyncpg.Connection, seen: dict[str, dict]) -> dict[str, tuple[float, float]]:
    """(max, min) served price per symbol since the user's snapshot time — from the quote ring, capped by
    the retention window. ONE round-trip for all symbols: the per-symbol snapshot times are passed as
    parallel arrays and joined via unnest (a per-symbol query was an N+1 that made /state take seconds
    at 30 symbols when the DB is in another region). Uses the (symbol, event_time) index."""
    if not seen:
        return {}
    syms = list(seen)
    sinces = [_snap_time(seen[s]) for s in syms]  # _read_state already dropped malformed rows
    rows = await conn.fetch(
        """
        select q.symbol, max(q.price) as hi, min(q.price) as lo
        from unnest($1::text[], $2::timestamptz[]) as v(symbol, since)
        join quotes q on q.symbol = v.symbol and q.event_time > v.since
        where not q.is_suspect and q.role = 'primary'
        group by q.symbol
        """,
        syms, sinces,
    )
    return {r["symbol"]: (float(r["hi"]), float(r["lo"])) for r in rows if r["hi"] is not None}


def _build_signal(lq: LatestQuote, snap: dict, b: Baselines, idx_now: float | None,
                  peaks: tuple[float, float] | None = None) -> Signal | None:
    seen_time = _snap_time(snap)
    if seen_time is None:
        return None
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
            spiked = abs(peak_pct) >= abs(trough_pct)
            ext = peak_pct if spiked else trough_pct
            reasons.append("Spiked, then came most of the way back" if spiked else "Dropped, then came most of the way back")
            path_note = f"{'spiked' if spiked else 'dropped'} {ext:+.1f}% then retraced ({excursion / sigma_eff:.1f}σ path move)"
            path_bonus = min(excursion / sigma_eff, PATH_BONUS_CAP)   # the path WAS the event; rank it like one

    return Signal(
        reasons=reasons,
        is_meaningful=scoring.is_meaningful(f) or path_bonus > 0,   # volume alone never promotes
        unusualness=round(scoring.unusualness(f) + path_bonus, 3),
        explain=Explain(
            sigma_move=round(f.abs_resid_z, 2), move_pct=round(f.move_pct * 100, 3),
            market_adjusted_pct=round(f.resid_pct * 100, 3), vol_ratio=round(f.vol_ratio, 2),
            crossed=f.crossed, sigma_daily_pct=round(b.ret_stdev_daily * 100, 2),
            beta=round(b.beta, 2) if b.beta is not None else None, elapsed_seconds=round(elapsed, 0),
            peak_pct=peak_pct, trough_pct=trough_pct, path_note=path_note,
        ),
    )


async def _q(fn, *args):
    """Run one read on its own pooled connection — lets independent reads proceed concurrently."""
    async with db.pool().acquire() as conn:
        return await fn(conn, *args)


async def list_watchlist(user_id: str) -> list[WatchRow]:
    """Two waves of CONCURRENT reads instead of eight sequential ones. Each read is independent within a
    wave, so wall time is ~one round-trip per wave — which matters when the database is in another
    region (~200ms per hop turned a 30-symbol /state into seconds). Pool max_size covers the fan-out."""
    now = datetime.now(UTC)
    # Wave 1: what does the user watch, what did they last see, what's snoozed.
    holdings, seen, snoozes = await asyncio.gather(
        _q(_holdings, user_id), _q(_read_state, user_id), _q(_snoozes, user_id))
    symbols = list(holdings)
    if not symbols:
        return []
    # Wave 2: everything keyed by those symbols.
    latest, bases, quarantined, peaks, disputed = await asyncio.gather(
        _q(quotes.latest_quotes, symbols + [INDEX_SYMBOL]),
        _q(baselines.get_baselines, symbols),
        _q(_quarantined_recent, symbols),
        _q(_peaks_since, seen),
        _q(quotes.disputes, symbols, settings.dispute_threshold_pct, settings.dispute_window_seconds),
    )
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


async def snooze(user_id: str, symbol: str, minutes: int | None) -> bool:
    """Hold a symbol out of 'needs your attention' until then (None clears). It stays listed — nothing hidden."""
    symbol = normalize(symbol)
    until = (datetime.now(UTC) + timedelta(minutes=minutes)) if minutes else None
    async with db.pool().acquire() as conn:
        status = await conn.execute("update watchlist_items set snoozed_until=$3 where user_id=$1 and symbol=$2",
                                    user_id, symbol, until)
    return _updated(status)


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


async def mark_seen(user_id: str, symbol: str) -> bool:
    """Freeze the quote the user is currently seeing (and the index level) as their baseline.
    False if the symbol isn't on their list — a snapshot for something you don't watch is an orphan."""
    symbol = normalize(symbol)
    async with db.pool().acquire() as conn:
        watched = await conn.fetchval("select 1 from watchlist_items where user_id=$1 and symbol=$2", user_id, symbol)
    if not watched:
        return False
    await _ensure_quote(symbol)
    async with db.pool().acquire() as conn:
        latest = await quotes.latest_quotes(conn, [symbol, INDEX_SYMBOL])
        if symbol in latest:
            idx = latest[INDEX_SYMBOL].price if INDEX_SYMBOL in latest else None
            await _snapshot_upsert(conn, user_id, symbol, latest[symbol], idx)
    return True


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
COHORT_TTL_S = 3600         # cohorts change when the watchlist changes (key) or daily as candles refresh
COHORT_CACHE_MAX = 256      # one entry per distinct watchlist-set; bounded so a churny user can't grow it forever


async def _cohort_model(conn: asyncpg.Connection, symbols: list[str]) -> cohorts.CohortModel | None:
    """The user's co-movement cohorts from cached REAL candles, memoized per watchlist-set for an hour."""
    key = frozenset(symbols)
    hit = _COHORT_CACHE.get(key)
    if hit and time.time() - hit[0] < COHORT_TTL_S:
        return hit[1]
    cands = {s: c for s, c in (await baselines.load_candles_many(conn, symbols)).items() if c}  # one round-trip
    model = cohorts.build(cands) if len(cands) >= 2 else None
    if len(_COHORT_CACHE) >= COHORT_CACHE_MAX:
        _COHORT_CACHE.pop(min(_COHORT_CACHE, key=lambda k: _COHORT_CACHE[k][0]))  # evict the oldest
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


def _headline(sig: Signal, ch: Change) -> str:
    """ONE lead reason on the card (the rest are chips), or the plain move if nothing promoted it.
    Volume is never the lead: it can't put a symbol in front of you by itself (see scoring.is_meaningful)."""
    lead = next((r for r in sig.reasons if not r.startswith("Volume")), None)
    if lead:
        return lead
    base = f"{ch.pct:+.2f}% since you last looked"
    return base if sig.reasons else base + " — nothing unusual"


def _changes_from_rows(rows: list[WatchRow], model: cohorts.CohortModel | None = None) -> list[ChangeRow]:
    """'While you were away'. Every moved symbol is included (rerank-never-suppress); is_meaningful marks
    the ones where a reason fired. With a cohort model: a symbol moving ALONE vs its co-movement peers
    gets that as a reason and its peer divergence adds to its rank; pack moves are tagged with their
    cohort so the client can collapse them. Grouping is presentation only — nothing is ever removed."""
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
        if r.snoozed_until is not None:
            # Snoozed: still LISTED with its reasons (nothing hidden), but not counted as needing attention.
            sig = sig.model_copy(update={"is_meaningful": False})
        out.append(ChangeRow(symbol=r.symbol, price=r.price, provenance=r.provenance, last_seen=snap,
                             change_since_seen=ch, signal=sig, headline=_headline(sig, ch),
                             quantity=r.quantity, impact_inr=r.impact_inr, snoozed_until=r.snoozed_until))

    if model is not None and out:
        moves = {c.symbol: c.change_since_seen.pct / 100.0 for c in out}
        elapsed = float(np.median([c.signal.explain.elapsed_seconds for c in out])) or scoring.MIN_ELAPSED_S
        res = cohorts.peer_residuals(model, moves, elapsed_seconds=elapsed,
                                     trading_day_s=scoring.TRADING_DAY_S, min_elapsed_s=scoring.MIN_ELAPSED_S)
        by_sym = {c.symbol: c for c in out}
        for c in out:
            c.cohort_id = model.cohort_of.get(c.symbol)
            pr = res.get(c.symbol)
            if pr is not None:
                c.peer_residual_z = round(float(pr[1]), 2)
        # Which ones are "moving alone"? |peer z| >= 2. In a PAIR both members diverge from each other by
        # construction, so only the one with the larger own move earns the flag ("X diverging from Y").
        for members in model.cohorts:
            present = [by_sym[s] for s in members if s in by_sym]
            flagged = [c for c in present if c.peer_residual_z is not None and abs(c.peer_residual_z) >= cohorts.PEER_Z_FLAG]
            if len(present) == 2 and len(flagged) == 2:
                flagged = [max(flagged, key=lambda c: (c.signal.explain.sigma_move, abs(c.peer_residual_z or 0), c.symbol))]
            for c in flagged:
                c.moving_alone = True
                if len(present) == 2:
                    other = next(o for o in present if o is not c).symbol.replace(".NS", "")
                    c.signal.reasons.append(f"Diverging from {other}, which it usually moves with")
                else:
                    c.signal.reasons.append("Moving alone — its usual peers aren't")
                if c.snoozed_until is None:   # snoozed stays listed-not-counted, even if it diverges
                    c.signal.is_meaningful = True
                c.headline = _headline(c.signal, c.change_since_seen)

    def rank(c: ChangeRow) -> float:
        # Peer divergence is unusualness too (same sigma-ish units), so it ADDS to the rank rather than
        # hard-overriding it: a 2.1-sigma lone mover you don't hold does not outrank a 2.5-sigma move on a
        # Rs 1L position. Exposure keeps its say.
        alone = abs(c.peer_residual_z) if (c.moving_alone and c.peer_residual_z is not None) else 0.0
        return attention_score(c.signal.unusualness + alone, c.impact_inr)

    out.sort(key=lambda c: (1 if c.snoozed_until is not None else 0, -rank(c), -abs(c.change_since_seen.pct)))
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
