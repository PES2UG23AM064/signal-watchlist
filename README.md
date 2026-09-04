# Signal — a smart market watchlist

> Most watchlists tell you the *price*. Signal tells you **what changed that you should care about
> since you last looked** — measured against each stock's own volatility, the market's move, and
> what *you personally* already saw — and it never shows a number without telling you how fresh it is.

Built for the **Code by Groww 2026** challenge. This is not the obvious watchlist: the headline view
is a ranked **"While you were away"** triage feed, not an alphabetical price grid.

## The core idea

"Since you last looked" can't be a timestamp — the market data at that moment is gone. So Signal
persists a **snapshot of what you actually saw** (price + the event-time it was as-of) and diffs the
live quote against *that*. Marking a symbol **Seen** freezes your baseline; the watermark only ever
advances forward, so a second device (or a stale write) can never move it backward.

## Status — milestone-based build

- **M1 (done):** end-to-end spine — identity (username + hashed PIN, cross-device), watchlist CRUD,
  the snapshot-based "changed since last seen" diff, the "While you were away" digest, deployed-ready.
- **Next:** data provenance & stale-write rejection · shared per-symbol poller · the
  volatility-normalized, *explainable* meaningfulness score · live provider + resilience · real-time
  push · property-based invariant tests. (See the design notes, expanded each milestone.)

## Architecture (M1)

```
React + Vite + Tailwind (mobile-first)  ──HTTP──►  FastAPI (async)  ──►  Supabase Postgres
        │                                              │
        └── client polling (SSE later)                 └── MarketDataProvider interface
                                                            └── ReplayProvider (deterministic backbone)
```

- **One datastore (Postgres).** No Redis: at this scale Postgres covers fan-out (LISTEN/NOTIFY) and
  coordination (advisory locks) with far less operational surface — documented as the scale-out path,
  not built prematurely.
- **asyncpg with explicit SQL**, no ORM: every query — including the monotonic-watermark guard — is
  visible and defensible.
- **Deterministic Replay provider** is the primary data backbone: the app is alive whenever you look
  (even with NSE closed), and the demo tells the same story every run. Live Yahoo data plugs in behind
  the same interface as opportunistic enrichment (later milestone).

## Run it locally

**Backend** (needs a Postgres `DATABASE_URL` — Supabase session-mode connection string):
```bash
cd backend
python -m venv .venv && ./.venv/Scripts/activate      # Windows; use source .venv/bin/activate on *nix
pip install -r requirements.txt
cp .env.example .env        # then paste your DATABASE_URL (URL-encode any '@' in the password as %40)
python -m uvicorn app.main:app --reload --port 8000
```
Migrations run automatically on startup. Verify the full flow end-to-end:
```bash
python -m scripts.smoke
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev            # http://localhost:5173  (set VITE_API_URL if the API isn't on :8000)
```

## Tech

FastAPI · asyncpg · Pydantic · Supabase Postgres · React · Vite · Tailwind CSS.
