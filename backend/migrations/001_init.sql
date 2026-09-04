-- M1 schema: identity, watchlist, per-user read state (the "what you last saw" snapshot).
-- Idempotent so it is safe to re-run.

create table if not exists users (
    id            uuid primary key default gen_random_uuid(),
    username      text unique not null,
    pin_hash      text not null,
    session_token text unique not null,
    created_at    timestamptz not null default now()
);

create table if not exists watchlist_items (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references users(id) on delete cascade,
    symbol     text not null,
    created_at timestamptz not null default now(),
    unique (user_id, symbol)          -- idempotent add; one row per (user, symbol)
);

create index if not exists idx_watchlist_user on watchlist_items (user_id);

-- read_state is the crux of "since you last looked": we persist a SNAPSHOT of what the user
-- actually saw (price + the event_time it was as-of), not just a timestamp. The watermark is
-- monotonic (advanced only forward) so concurrent "mark seen" from two devices is safe.
create table if not exists read_state (
    user_id              uuid not null references users(id) on delete cascade,
    symbol               text not null,
    watermark_event_time timestamptz not null,
    snapshot_json        jsonb not null,   -- {price, event_time, source}
    seen_at              timestamptz not null default now(),
    primary key (user_id, symbol)
);
