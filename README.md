# Signal — a smart market watchlist

A watchlist that answers one question well: **what changed that I should care about since I last
looked?** Built for the Code by Groww 2026 challenge.

The core technical bet is small and specific: **"since you last looked" cannot be a timestamp.** The
market data from that moment is already gone, so you can't recompute the diff later. Signal instead
persists a *snapshot of what you actually saw* (the price and the event-time it was as-of) and diffs
the live quote against that snapshot. Marking a symbol **Seen** freezes your baseline; the baseline
only ever moves forward, so a second device — or a delayed/out-of-order quote — can never quietly
move it backward. The headline view is a ranked **"While you were away"** list, not a price grid.

## What's built (and honest about what isn't)

This is an in-progress, milestone-based build. To keep the README trustworthy, here is exactly what
the code does today versus what is still planned.

**Built and working:**
- **Identity that follows you across devices** — username + hashed PIN (bcrypt), server-issued session
  token. Not OAuth (a deliberate scope choice; see trade-offs).
- **Watchlist CRUD** — idempotent add, symbol normalization (`reliance` → `RELIANCE.NS`).
- **Snapshot-based "changed since last seen"** — per-user snapshot (price + index level + event-time) with
  a monotonic watermark (the core bet above). `backend/app/services.py`.
- **The meaningful-change scoring engine** (`backend/app/scoring.py`) — see the next section. Every flag
  carries a plain-English reason and a tap-to-expand panel with every number behind it.
- **Real baselines from real data** — a year of actual NSE daily candles per symbol (Yahoo, one
  unauthenticated endpoint), cached in Postgres: daily volatility, 20-day volume, 52-week range, and beta
  vs NIFTY. These are the denominators, so "2σ" means 2σ *of this stock's real history*.
- **A backtested, honest ML component** — see the next section. The two hypotheses that failed are shown
  in the app, not hidden.
- **Shared per-symbol poller** — one in-process asyncio task polls each *unique* symbol (plus NIFTY) once
  per cycle, regardless of how many users watch it (fan-in). Survives bad cycles; prunes old rows.
- **Quotes as a time-series** with idempotent ingestion; the latest served value is always the one with
  the greatest event-time, so a stale/out-of-order arrival structurally can't become what you see.
- **Honest data-state** — three independent axes, all shown: **market status** (real IST hours + a 2026
  NSE holiday table), **freshness** (age of the quote), **source** (a loud "simulated" chip when data
  is from the Replay generator rather than a live feed).
- **Sanity quarantine** — impossible/garbage quotes (≤0, negative volume, future timestamps, and
  single-tick jumps beyond NSE's 20% circuit band) are flagged and stored for audit but never served.
- **Demo rewind** (`POST /dev/rewind`) — the simulator is a pure function of time, so "pretend I last
  looked 15 minutes ago" is reconstructed *exactly*, on demand.
- **Tests** — 22 unit tests, including a **no-look-ahead test** on the feature code (written first) and
  sanity/market-hours invariants; plus an end-to-end smoke script (`scripts/smoke.py`).

**Planned, NOT yet built (do not assume these exist in the code):**
- Exposure weighting (rank by move × how much you hold) and per-signal acknowledge/decay.
- Live **Yahoo** quotes as the primary feed with circuit-breaker fallback, and cross-source
  "disputed" reconciliation. (Yahoo is used today for the real candle history, not live ticks.)
- A live fault-injection toggle in the UI.
- Horizontal scale-out (see trade-offs) — designed, not implemented. Real-time push (SSE) — cut on purpose.

## How "meaningful" is decided — and what we tested

The ranking is **descriptive**: how unusual was what *already happened*, in this stock's own terms?
- **Market-adjusted move in σ**: `|move − β·NIFTY move| / (σ_daily · √elapsed)` — a 2% move on a stock
  that moves 1.3%/day is different from 2% on one that moves 3%/day, and a move the whole market made
  isn't news about this stock. √time scaling puts a 4-hour move and a 3-day move on one scale.
- **Volume vs its 20-day average**, and **52-week breaks since you last looked**.
- Flags are fixed thresholds (≥2σ, ≥1.5× volume, a break). Ranking weights are a transparent 1:1:1.

Then we asked whether any of that **predicts** anything, with a real backtest (`backend/ml/train_scorer.py`):
the *same* feature code, run over a year of real candles for 9 NSE stocks (1,665 symbol-days), split by
time with an embargo, out-of-sample, three pre-registered questions:

| Question | Result |
|---|---|
| Does an unusual move predict more big moves over the next 3 days? | **No edge** (AUC 0.48) |
| Does it predict the *direction* of the next move? | **No edge** (AUC 0.48) — so this app never tells you what to buy |
| Does it predict an unusually *active* period (volatility clustering)? | **Weak, real** (AUC 0.54, 1.46× top-decile lift), driven by volume |

So the ranking stays descriptive — the data gives no basis for "learned predictive weights," and we don't
pretend otherwise. The one learned signal that ships is the **activity outlook** tag: a 3-coefficient
logistic regression exported as JSON (`app/model/scoring_model.json`); production evaluates a dot
product and a sigmoid — no scikit-learn at runtime. The full report is served at `GET /model` and shown
in the app. Sample-size honesty: 1,665 symbol-days, but overlapping label windows and correlated
large-caps put the effective sample in the low hundreds — which is why it's 3 features, not 30. The
simulator is **never** used to train or validate anything.

## Architecture (today)

```
React + Vite + Tailwind (mobile-first)  ──HTTP──►  FastAPI (async)  ──►  Supabase Postgres
        │  polls /state every 5s                       │
        │                                              ├── in-process poller (asyncio, lifespan)
        └── (real-time push is a later milestone)      └── MarketDataProvider interface
                                                            └── ReplayProvider (deterministic backbone)
```

The whole thing — API + ingestion poller — runs as **one process / one container**. That's a
deliberate choice, not a limitation I'm hiding:

- **One datastore (Postgres), no Redis.** Single process means fan-out is just an in-process broadcast
  and there's exactly one poller by construction, so no external message bus or leader election is
  needed *yet*. The multi-instance path — Postgres `LISTEN/NOTIFY` for fan-out and `pg_advisory_lock`
  for poller election — is understood but **not built**. (On Supabase it would require the session
  pooler on port 5432, because the transaction pooler on 6543 drops the session that `LISTEN/NOTIFY`
  and session advisory locks depend on.)
- **asyncpg with explicit SQL, no ORM** — every query is visible, including the integrity-critical
  monotonic-watermark upsert.
- **Known scale limit:** the poller loop is the real bottleneck. It fetches symbols per cycle; once the
  number of unique symbols makes a cycle exceed the poll interval, ingestion falls behind. That's the
  honest breaking point, and it's where the multi-instance path above would come in.
- **A conflict we actually hit:** during development a local poller and the deployed one — running
  different versions with different price anchors — wrote to the same table, and "latest = max(event-time)"
  flipped between them (a 43% swing on one symbol). It's the brief's "conflicting data" case in the
  wild. Two fixes: the tick-jump quarantine now sits at NSE's real 20% circuit band, so a misbehaving
  writer's rows are quarantined instead of served; and exactly one writer per environment (the
  multi-instance design uses advisory-lock election for this).

## Run it locally

**Backend** (needs a Postgres `DATABASE_URL` — a Supabase session-mode connection string):
```bash
cd backend
python -m venv .venv && ./.venv/Scripts/activate     # Windows; source .venv/bin/activate on *nix
pip install -r requirements.txt
cp .env.example .env       # paste your DATABASE_URL (URL-encode any '@' in the password as %40)
python -m uvicorn app.main:app --reload --port 8000
```
Migrations run automatically on startup. End-to-end check: `python -m scripts.smoke`. Unit tests: `pytest`.

**Frontend:**
```bash
cd frontend
npm install
npm run dev            # http://localhost:5173  (set VITE_API_URL if the API isn't on :8000)
```

## Tech

FastAPI · asyncpg · Pydantic · Supabase Postgres · React · Vite · Tailwind CSS · deployed on Render.
