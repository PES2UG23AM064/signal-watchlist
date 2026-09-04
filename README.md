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
- **Snapshot-based "changed since last seen"** — per-user snapshot + monotonic watermark (the core bet
  above). This is the part worth reading: `backend/app/services.py`.
- **Shared per-symbol poller** — one in-process asyncio task polls each *unique* symbol once per cycle,
  regardless of how many users watch it (fan-in). Survives bad cycles; prunes old rows.
- **Quotes as a time-series** with idempotent ingestion; the latest served value is always the one with
  the greatest event-time, so a stale/out-of-order arrival structurally can't become what you see.
- **Honest data-state** — three independent axes, all shown: **market status** (real IST hours + a 2026
  NSE holiday table), **freshness** (age of the quote), **source** (a loud "simulated" chip when data
  is from the Replay generator rather than a live feed).
- **Sanity quarantine** — impossible/garbage quotes (≤0, negative volume, absurd single-tick jumps,
  future timestamps) are flagged and stored for audit but never served as truth.
- **Tests** — unit tests for the market/sanity logic; an end-to-end smoke script (`scripts/smoke.py`).

**Planned, NOT yet built (do not assume these exist in the code):**
- The real *meaningful-change scoring engine* — today "meaningful" is a naive percentage threshold
  (`_NAIVE_MEANINGFUL_PCT`). The volatility-normalized z-score, volume anomaly, level crossings,
  index-relative strength, and exposure weighting are the next milestone.
- The live **Yahoo** provider + real daily-candle baselines, and cross-source conflict reconciliation.
- Per-signal acknowledge/decay, the explainability panel, and a live fault-injection toggle.
- Horizontal scale-out (see trade-offs) — designed, not implemented.

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
