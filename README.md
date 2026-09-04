<div align="center">

# Signal

**A watchlist that answers one question well:<br>*what changed that I should care about since I last looked?***

[![CI](https://github.com/PES2UG23AM064/signal-watchlist/actions/workflows/ci.yml/badge.svg)](https://github.com/PES2UG23AM064/signal-watchlist/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![React 18](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![Postgres](https://img.shields.io/badge/Postgres-Supabase-4169E1?logo=postgresql&logoColor=white)
![Tests](https://img.shields.io/badge/tests-45_unit_%2B_4_integration-brightgreen)

**[Live app](https://signal-watchlist-web.onrender.com)** · **[API docs](https://signal-watchlist-api.onrender.com/docs)**

*Built for the Code by Groww 2026 challenge. Open the live app on a phone.*

</div>

---

## Why this exists

Every watchlist shows you a grid of prices. None of them tell you what *happened* while you were away — and
on a red day, all of them scream about every stock at once, which is exactly when you most need triage.

Signal replaces the price grid with a ranked **"While you were away"** digest. Each card says **one thing in
plain English** ("Unusual move for this stock, even after the market's move"), and every number behind that
sentence is one tap away. Nothing is a black box, nothing is hidden, and the app never shows a price without
telling you how fresh it is and where it came from.

### The core technical bet

> **"Since you last looked" cannot be a timestamp.**

The market data from that moment is already gone, so you can't recompute the diff later. Signal persists a
**snapshot of what you actually saw** — price, index level, and the event-time it was as-of — and diffs the
live quote against that. Marking a symbol **Seen** freezes your baseline. The baseline is a **monotonic
watermark**: it only ever moves forward, so a second device or a delayed, out-of-order quote can never
quietly move it backward. That single design choice is what makes cross-device state, the demo rewind, and
the integrity tests possible.

---

## Table of contents

- [Quick start](#quick-start)
- [What it does](#what-it-does)
- [How "meaningful" is decided](#how-meaningful-is-decided)
- [The ML story: what we tested, what shipped, what didn't](#the-ml-story-what-we-tested-what-shipped-what-didnt)
- [Architecture](#architecture)
- [API reference](#api-reference)
- [Configuration](#configuration)
- [Testing & CI](#testing--ci)
- [Deployment](#deployment)
- [Project structure](#project-structure)
- [Design decisions & incidents](#design-decisions--incidents)
- [Not built, on purpose](#not-built-on-purpose)

---

## Quick start

You need **Python 3.12**, **Node 22**, and a Postgres `DATABASE_URL` (a free [Supabase](https://supabase.com)
project works — use the **session-mode** connection string on port 5432; see [Configuration](#configuration)
for why).

**1. Backend** (API + the in-process ingestion poller)

```bash
cd backend
python -m venv .venv
./.venv/Scripts/activate            # Windows   |   source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env                 # paste your DATABASE_URL (URL-encode any '@' in the password as %40)
python -m uvicorn app.main:app --reload --port 8000
```

Migrations run automatically on startup. The API is at `http://localhost:8000` (interactive docs at `/docs`).

**2. Frontend**

```bash
cd frontend
npm install
npm run dev                          # http://localhost:5173  (set VITE_API_URL if the API isn't on :8000)
```

**3. Use it.** Open the app, create an account (email + password), add `RELIANCE`, `TCS`,
`INFY`, `HDFCBANK`, `ICICIBANK`. Tap **Mark all seen**, then open the **Demo** drawer and press
*"pretend I last looked 15 minutes ago"* — the digest fills in with exactly what you would have seen.

**Verify the install**

```bash
cd backend
pytest tests -q --ignore=tests/integration      # 45 unit tests, no DB needed (~1s)
python -m scripts.smoke                          # end-to-end smoke against your running API
```

---

## What it does

### The product

| | |
|---|---|
| **"While you were away" digest** | A ranked list of what changed since your snapshot, not a price grid. Each card leads with one plain-English reason; a tap opens every number behind it. |
| **Identity that follows you** | Email + bcrypt-hashed password (explicit *create account* vs *sign in* — a typo can't create a ghost account), server-issued session token. Tokens expire (30 days) and rotate on every login; logout rotates server-side so a copied token dies everywhere; 5 wrong passwords lock the account for 15 minutes; sign-in errors are generic so the API never reveals which emails exist. |
| **Snapshot-based baseline** | Per-(user, symbol) snapshot of price + index level + event-time, with a monotonic watermark. Works across devices; can't be moved backward. |
| **Exposure weighting** | Optionally record how many shares you hold. Held symbols show the **₹ impact since you last looked** and rank by a transparent attention score: `unusualness × (1 + log₁₀(1 + |₹ impact| / 1000))` — roughly ×2 at ₹10k at stake. Holdings *amplify* an unusual move; they never drown one out (tested). |
| **Co-movement cohorts** | Stocks that move as a pack fold into one card ("2 moving together"); the stock moving *alone* is promoted. Learned from your own symbols' return correlations — no sector table. Nothing is ever hidden (tested). |
| **Path since you looked** | From the quote ring: *"spiked +2.4% then retraced (3.1σ path move)"* — a ≥2σ excursion is reported even when the endpoint diff would hide it. |
| **Snooze** | Hold a symbol out of "needs your attention" for an hour. It stays listed; nothing disappears. |
| **Real-time push (SSE)** | The poller publishes a tick after each cycle; clients refetch `/state`. The stream carries no user data (so no token in any URL). Polling stays as the fallback transport. |

### Honest data state — three independent axes, always shown

| Axis | What it tells you |
|---|---|
| **Market status** | Real IST trading hours + a 2026 NSE holiday table. |
| **Freshness** | Age of the served quote: **live** (≤20s) → **delayed** (≤120s) → **stale**. |
| **Source** | A loud **"simulated"** chip whenever data comes from the Replay generator rather than a live feed. |

### Data integrity & resilience

| | |
|---|---|
| **Quotes as a time-series** | Idempotent ingestion; the served value is always the one with the greatest event-time, so a late/out-of-order arrival structurally can't become what you see. |
| **Sanity quarantine** | Impossible quotes — price ≤ 0, negative volume, future timestamps, single-tick jumps beyond NSE's **20% circuit band** — are flagged and stored for audit but **never served**. Judged only against a *recent* reference, so a stale wrong price can't quarantine correct data forever. |
| **Live feed + circuit breaker** | `MARKET_PROVIDER=composite`: live Yahoo quotes while NSE is open; on failure the breaker trips (**3 strikes → 60s open → one half-open trial**) and every quote falls back to the simulator without the app ever erroring — and the source badge tells the truth about which path served it. |
| **Cross-source reconciliation** | An optional second real feed (Twelve Data) is recorded as `role='secondary'` — a cross-check that can **never** become the served price. When it diverges from the primary beyond a threshold, the symbol shows **"disputed"** with both prices. We never silently pick one. |
| **Poller leader election** | A Postgres session-level advisory lock on a dedicated connection guarantees **exactly one instance writes**, even if several run; a follower takes over within one poll interval if the leader dies. Tested across two sessions. |
| **Rate limiting** | The live feed runs behind a token bucket (30/min) with bounded concurrency. |
| **Observability** | `GET /status` + a **System health** panel: provider route, breaker state, poller role, cycle time vs interval (with a falling-behind warning), per-symbol freshness, quarantines in the last hour. |

### Break it on purpose

Resilience is demonstrated, not described. `POST /dev/inject` (and the **"Break it on purpose"** panel,
shown only when data is simulated) sends faults through the **real** ingestion path:

| Kind | What happens | What you see |
|---|---|---|
| `garbage` | price = 0 tick | Quarantined; served price unchanged |
| `jump` | +35% in one tick | Quarantined (beyond the 20% band); served price unchanged |
| `future` | event-time +1 hour | Quarantined; can't poison latest-by-event-time |
| `stale` | Pause this symbol's upstream ~2.5 min | Badge goes live → delayed → stale, then recovers |
| `conflict` | A second feed that disagrees by 5% | Card shows **"disputed · 2nd feed ₹X"**; primary still served |

`POST /dev/rewind` reconstructs your snapshots as of N minutes ago — exactly, because the simulator is a pure
function of time. (Refuses to run when a live feed is the active source; the two would be inconsistent.)

---

## How "meaningful" is decided

The ranking is **descriptive**: how unusual was what *already happened*, in this stock's own terms? All
denominators come from **real data** — a year of actual NSE daily candles per symbol (Yahoo, one
unauthenticated endpoint), cached in Postgres: daily volatility, 20-day volume, 52-week range, beta vs NIFTY.
So "2σ" means 2σ *of this stock's real history*.

| Signal | Definition | Flag |
|---|---|---|
| **Market-adjusted move** | `\|move − β·NIFTY move\| / (σ_daily · √elapsed)` — a move the whole market made isn't news about this stock; √time scaling puts a 4-hour move and a 3-day move on one scale | ≥ 2σ |
| **Volume** | vs this stock's 20-day average | ≥ 1.5× |
| **52-week break** | New high/low since your snapshot | any |
| **Path excursion** | Peak move since you looked, from the quote ring — a stock that ran +3% and came back flat still *happened* | ≥ 2σ |
| **Moving alone** | Peer-residual z-score vs its cohort (see below) — *this is about that stock, not the market* | ≥ 2 |

Flags are fixed thresholds. Ranking weights are a transparent 1:1:1 in σ-equivalent units, plus a *capped*
term for a retraced spike and the peer-divergence term. The card shows one lead reason; the panel shows every
number.

---

## The ML story: what we tested, what shipped, what didn't

### Prediction: tested honestly, and it failed

Before trusting "meaningful", we asked whether it **predicts** anything, with a real backtest
([`backend/ml/train_scorer.py`](backend/ml/train_scorer.py)):

- the **same feature code** the app uses (no look-ahead — unit-tested, test written first)
- a year of real daily candles for **29 NSE symbols**, **5,249 scoreable symbol-days** (Dec 2025 – Sep 2026)
- time-ordered split with a 3-day embargo, out-of-sample, **three pre-registered questions**
- confidence intervals via **block bootstrap over 3-day blocks of trading days** (all symbols of a day
  resampled together) — a plain row bootstrap would pretend 5,000 correlated rows are independent

| Question | Test AUC (95% CI) | Verdict |
|---|---|---|
| Does an unusual move predict more big moves over the next 3 days? | 0.51 (0.47 – 0.55) | **No edge** |
| Does it predict the *direction* of the next move? | 0.52 (0.48 – 0.55) | **No edge** — so this app never tells you what to buy |
| Does it predict an unusually *active* period (volatility clustering)? | 0.54 (0.50 – 0.57), top-decile lift 1.3× | **No edge** by the CI; far below the bar |

**So no learned model ships.** The bar was fixed before looking: a predictive tag needs a CI that excludes
0.5 *and* top-decile lift ≥ 2×. Nothing came close, and a 0.54-AUC "outlook" tag would be noise dressed as
insight. That is the answer to "why no ML on the ranking" — not caution, evidence. The full report is served
at `GET /model` and shown in the app's **Insights** tab. The two hypotheses that failed are displayed, not
hidden.

### Structure: where ML *does* work here

Indian retail watchlists are mostly correlated large-caps: on a red day everything is red and a naive digest
screams N times. [`backend/app/cohorts.py`](backend/app/cohorts.py) clusters *your own* watched symbols by
daily-**return** correlation over the real candles — average-linkage agglomerative clustering, merge while
mean correlation ≥ 0.5, ~15 lines of numpy at runtime, no labels, no sector table. Then:

- symbols in one cohort moving the same way **fold into one card** — *"moved as a pack, not one stock's news"*;
- a symbol moving far from its peers (**peer-residual z ≥ 2**) gets **"moving alone"** as a reason and that
  divergence adds to its rank. In a pair, only the member with the larger own move is promoted — you can't
  call both "alone".

Validation on the real candles ([`backend/ml/validate_cohorts.py`](backend/ml/validate_cohorts.py), shown in
the app). From returns alone it found:

| Cohort | Members |
|---|---|
| Banks | `AXISBANK` `HDFCBANK` `ICICIBANK` |
| IT | `HCLTECH` `INFY` `TCS` `TECHM` `WIPRO` |
| Metals / infra | `JSWSTEEL` `TATASTEEL` `LT` `ULTRACEMCO` |
| Power | `NTPC` `POWERGRID` |

- **Within-cohort correlation 0.61 vs 0.20 across**
- Half-year vs half-year stability is **modest** (ARI 0.30; 15 of 29 symbols kept their grouping) — thin
  windows flip borderline members, and we say so
- Replaying the year: digest cards fell **1,152 → 987 (−14%)**, with **103 "moving alone" flags and 0 ever
  folded into a group** — an invariant, not a claim

Grouping is presentation only: every symbol stays in the list with its own reasons. The simulator is
**never** used to train or validate anything. Sample-size honesty: overlapping label windows and correlated
large-caps put the effective sample far below 5,249 — which is why it's 3 features, not 30, and why the CIs
are block-bootstrapped.

---

## Architecture

```
┌────────────────────────────────┐        ┌──────────────────────────────────────────────────────┐
│  React 18 + Vite + Tailwind    │  HTTP  │  FastAPI (async) — ONE process / ONE container         │
│  mobile-first                  │◄──────►│                                                        │
│                                │        │  ┌───────────────┐   ┌─────────────────────────────┐  │
│  GET /state on every SSE tick  │  SSE   │  │ API routers   │   │ In-process poller (asyncio) │  │
│  (5s / 30s polling fallback)   │◄───────│  │ auth·watchlist│   │ • advisory-lock leader       │  │
└────────────────────────────────┘        │  │ state·model   │   │ • one fetch per unique symbol│  │
                                          │  │ status·dev    │   │ • sanity quarantine          │  │
                                          │  └───────┬───────┘   │ • publishes tick on event bus│  │
                                          │          │           └──────────────┬──────────────┘  │
                                          │          │    MarketDataProvider    │                 │
                                          │          │   ┌──────────────────────┴───────────────┐ │
                                          │          │   │ Composite: Yahoo (live, breaker)     │ │
                                          │          │   │   ├─ fallback → Replay (simulated)   │ │
                                          │          │   │   └─ secondary → Twelve Data (audit) │ │
                                          │          │   └──────────────────────────────────────┘ │
                                          └──────────┼─────────────────────────────────────────────┘
                                                     ▼
                                          ┌──────────────────────────┐
                                          │  Supabase Postgres       │
                                          │  asyncpg, explicit SQL   │
                                          │  quotes · snapshots ·    │
                                          │  baselines · users       │
                                          └──────────────────────────┘
```

**Why one process, one datastore.** API + poller deploy as a single container; Postgres is the only
stateful service. That's a deliberate choice, not a limitation being hidden:

- **No Redis, no broker.** Fan-out is an in-process event bus feeding SSE. Exactly one poller writes,
  enforced by a **Postgres session-level advisory lock** — so a second instance is already safe today. The
  one remaining multi-instance step is swapping the bus for Postgres `LISTEN/NOTIFY` (same publish/subscribe
  surface). Both need Supabase's **session pooler on 5432**: the transaction pooler on 6543 recycles the
  physical session that advisory locks and `LISTEN/NOTIFY` live in.
- **Shared per-symbol poller (fan-in).** Each *unique* symbol (plus NIFTY) is fetched once per cycle no
  matter how many users watch it. Survives bad cycles; prunes rows older than 24h.
- **asyncpg with explicit SQL, no ORM.** Every query is visible, including the integrity-critical
  monotonic-watermark upsert.
- **Provider interface.** `MarketDataProvider` with three implementations: `replay` (deterministic simulator
  anchored to each stock's real last close and sized in its real σ), `yahoo` (live), `composite` (live +
  breaker + honest fallback + optional secondary).
- **Runtime ML is a JSON artifact + numpy.** scikit-learn is a dev-only dependency; production scoring is a
  dot product.

---

## API reference

All endpoints except `/auth/register`, `/auth/login`, `/health`, `/market`, `/status`, `/model`, `/events` require
`Authorization: Bearer <token>`. Interactive docs: [`/docs`](https://signal-watchlist-api.onrender.com/docs).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/register` | Create an account (email, password ≥ 8, optional display name) → session token; 409 if taken |
| `POST` | `/auth/login` | Sign in (email + password) → fresh session token; 401 generic, 429 when locked |
| `GET` | `/auth/me` | Validate a remembered token → `{email, display_name}` |
| `POST` | `/auth/logout` | Rotate the token server-side (kills it on every device) |
| `GET` | `/state` | **The one digest surface**: market status + watchlist + ranked changes + cohorts, one round trip |
| `GET` | `/watchlist` | List watched symbols |
| `POST` | `/watchlist` | Add a symbol (idempotent; `reliance` → `RELIANCE.NS`) |
| `DELETE` | `/watchlist/{symbol}` | Remove a symbol |
| `POST` | `/watchlist/{symbol}/seen` | Mark seen — advance this symbol's snapshot (monotonic) |
| `POST` | `/watchlist/seen-all` | Mark everything seen |
| `PATCH` | `/watchlist/{symbol}/quantity` | Record shares held (rupee-impact ranking) |
| `POST` | `/watchlist/{symbol}/snooze?minutes=60` | Hold out of "needs attention"; `0` clears |
| `GET` | `/events` | Server-Sent Events: `quotes_updated` tick after each poll cycle (no user data) |
| `GET` | `/model` | Backtest + cohort validation report (the "receipts") |
| `GET` | `/status` | Provider route, breaker state, poller role, cycle timing, freshness, quarantines |
| `GET` | `/market` | NSE open/closed with IST detail |
| `GET` | `/health` | Liveness + active provider |
| `POST` | `/dev/inject` | Fault injection: `garbage` · `jump` · `future` · `stale` · `conflict` |
| `POST` | `/dev/rewind?minutes=15` | Reconstruct snapshots as of N minutes ago (Replay only) |

Dev endpoints are auth-scoped (only touch the caller's own state), Replay-only, and can be disabled with
`ENABLE_DEV_ENDPOINTS=false`.

---

## Configuration

Backend settings are read from `backend/.env` (see [`.env.example`](backend/.env.example)).

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | — | **Required.** Supabase **session-mode** string (port 5432). Transaction mode (6543) breaks advisory locks. |
| `MARKET_PROVIDER` | `replay` | `replay` · `composite` · `yahoo`. The deployed demo runs `replay` on purpose: reproducible, and the rewind only makes sense on the simulator. |
| `REPLAY_SEED` | `42` | Deterministic seed — the demo tells the same story every run |
| `TWELVEDATA_API_KEY` | — | Optional second real feed (composite only). Free key at twelvedata.com |
| `DISPUTE_THRESHOLD_PCT` | `2.0` | Primary/secondary divergence beyond this ⇒ "disputed" |
| `DISPUTE_WINDOW_SECONDS` | `120` | Only compare quotes this close in time |
| `POLL_INTERVAL_SECONDS` | `5` | Poller cycle target; `/status` warns when a cycle exceeds it |
| `QUOTE_RETENTION_HOURS` | `24` | Time-series retention |
| `RUN_POLLER` | `true` | Set `false` to run the API without ingestion |
| `ENABLE_DEV_ENDPOINTS` | `true` | On for the hackathon demo |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated; `*.onrender.com` is allowed by regex |

Frontend: `VITE_API_URL` (defaults to `http://localhost:8000`).

---

## Testing & CI

```bash
cd backend
pytest tests -q --ignore=tests/integration            # 45 unit tests, ~1s, no database
INTEGRATION=1 pytest tests/integration -q              # 4 DB-backed invariant tests (needs DATABASE_URL)
ruff check app ml tests                                # lint
python -m scripts.smoke                                # end-to-end smoke against a running API
```

**Unit tests** cover the scoring math, the **no-look-ahead guarantee** on the feature code (written before
the backtest), sanity-quarantine rules, market-hours/holiday logic, the circuit breaker's state machine,
cohort clustering (including "nothing is ever hidden"), the ranking (holdings amplify but never drown out),
and the Replay simulator's determinism.

**Integration tests** ([`tests/integration/test_db_invariants.py`](backend/tests/integration/test_db_invariants.py))
prove the invariants this README claims against a **real Postgres**:

1. quarantine, roles, and disputes — a secondary quote can never be served;
2. the stale-reference escape hatch — a wrong old price can't quarantine correct data forever;
3. the **monotonic watermark** — a snapshot never moves backward;
4. **leader-lock exclusivity** across two sessions.

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on every push and PR: ruff + unit tests,
the integration suite against a Postgres 16 service container, and a production frontend build.

---

## Deployment

The live demo is two free [Render](https://render.com) services defined in [`render.yaml`](render.yaml):

| Service | What | Notes |
|---|---|---|
| `signal-watchlist-api` | FastAPI + in-process poller, one container | Health check on `/health`; set `DATABASE_URL` in the dashboard |
| `signal-watchlist-web` | Vite static build | Set `VITE_API_URL` to the API's URL, then redeploy |

Migrations are applied on startup. The API accepts any `https://*.onrender.com` origin so the two services
talk without hardcoding URLs.

**Measured scale limit.** With a 30-symbol watchlist against the live deployment, the poller cycle initially
hit 6.5s against a 5s interval and `/state` took 3–7s — the cause was N+1 round-trips × ~200ms cross-region
RTT (Render Oregon ↔ Supabase Singapore). Fixes shipped: the poller writes a whole cycle in one
`executemany`; peak-excursion reads are one `unnest` join; cohort candles load in one query; `/state` runs
its independent reads concurrently. Result: warm `/state` ~1s from a laptop in India, poller cycle well under
the interval. The remaining step is colocating app and database (a Render region setting, not code).

---

## Project structure

```
signal-watchlist/
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app; lifespan wires DB pool, migrations, poller
│   │   ├── config.py          Settings (pydantic-settings)
│   │   ├── auth.py            Email + bcrypt password, register/login, token lifecycle, lockout
│   │   ├── services.py        Watchlist, snapshots (monotonic watermark), digest assembly, ranking
│   │   ├── scoring.py         Market-adjusted σ move, volume ratio, 52-week breaks, flags
│   │   ├── cohorts.py         Return-correlation clustering; peer-residual "moving alone"
│   │   ├── baselines.py       Real daily candles → σ, 20-day volume, 52-week range, β vs NIFTY
│   │   ├── quotes.py          Time-series ingestion, sanity quarantine, freshness
│   │   ├── poller.py          Shared per-symbol poller with advisory-lock leader election
│   │   ├── events.py          In-process event bus → SSE
│   │   ├── market.py          NSE hours (IST) + 2026 holiday table
│   │   ├── providers/         MarketDataProvider: replay · yahoo · twelvedata · composite (breaker)
│   │   ├── routes/            auth · watchlist · state · model · status · events · dev
│   │   └── model/             backtest_report.json · cohort_report.json (served by /model)
│   ├── migrations/            001…008 plain SQL, applied on startup
│   ├── ml/
│   │   ├── train_scorer.py    Backtest: time split, embargo, block bootstrap, pre-registered labels
│   │   └── validate_cohorts.py  Cohort structure, stability (ARI), alert counterfactual
│   ├── scripts/smoke.py       End-to-end smoke test
│   └── tests/                 45 unit tests + tests/integration (4, DB-backed)
├── frontend/src/
│   ├── App.jsx                State, SSE + polling transport, drawers
│   ├── api.js                 API client
│   └── components/
│       ├── digest/            DigestFeed (ranked cards) · ExplainPanel (every number)
│       ├── watchlist/         WatchlistPanel (add, seen, holdings, remove)
│       ├── insights/          ModelReceipts · CohortLab · SystemHealth
│       ├── demo/              DemoDrawer — rewind + "Break it on purpose"
│       └── layout/, ui/       TopBar, SummaryStrip, Drawer, Badges, Legend
├── .github/workflows/ci.yml
└── render.yaml
```

---

## Design decisions & incidents

**Snapshot, not timestamp.** Covered above; it is the reason everything else works.

**Descriptive, not predictive.** The backtest said the ranking has no forecasting power, so the product
never claims any. It ranks what *was* unusual and shows its work.

**No LLM narration.** The scoring is auditable end to end and can be backtested. You cannot backtest a
narration. Against a field whose originality play is the LLM, this project chose a definition it can prove.

**Email + password, not OAuth.** A deliberate scope call: adding Google sign-in is a provider config, not
an architecture change, and the parts that are hard to retrofit — explicit sign-up vs sign-in, hashing off
the event loop, token expiry/rotation, lockout, non-enumerating errors — are real and tested against the DB.

**The simulator is labeled, anchored, and never trained on.** It says "simulated" everywhere, it's anchored
to each stock's real last close and sized in its real σ, and it exists so the product is demonstrable when
NSE is closed — which is most of the time.

**A conflict we actually hit.** During development a local poller and the deployed one — running different
versions with different price anchors — wrote to the same table, and "latest = max(event-time)" flipped
between them (a 43% swing on one symbol). It's the brief's "conflicting data" case in the wild. Three fixes:
the tick-jump quarantine now sits at NSE's real 20% circuit band, so a misbehaving writer's rows are
quarantined instead of served; the quarantine judges against a *recent* reference only (a stale wrong price
must not block correct data forever — hit that too); and exactly one writer per environment, enforced by
the advisory lock rather than by deployment discipline.

**Stated limitation: √t scaling intraday.** The move is normalized by σ·√elapsed. That's approximately
true intraday but not validated here — Yahoo gives 7 days of 1-minute bars, and validating on the simulator
would be circular.

---

## Not built, on purpose

| Idea | Why not |
|---|---|
| LLM narration | Can't be backtested; would undermine the auditable definition |
| Changepoint detection | The user's own snapshot *is* the changepoint they care about |
| EWMA thresholds | Makes the alert threshold itself volatile; alerts want a stable one |
| Volume seasonality | No real intraday history to calibrate on; the simulator would be circular |
| Per-signal acknowledge | Symbol-level Seen + snooze already cover the inbox semantics |
| Postgres `LISTEN/NOTIFY` fan-out | The bus interface is ready; one process doesn't need it yet |
| Redis / Kafka | Postgres is enough for fan-out at this scale; add it when it isn't, not before |

**With another week:** colocate app and DB; `LISTEN/NOTIFY` to run more than one instance; re-validate the
√t assumption on real intraday history; a broader symbol universe for the cohort model.

---

## Tech

FastAPI · asyncpg · Pydantic · numpy · Supabase Postgres · React 18 · Vite · Tailwind CSS · Render · GitHub Actions
