-- M2: the quotes time-series. The shared poller writes one row per (symbol, poll); reads serve the
-- latest by event_time. Because "latest = max(event_time)", an out-of-order/stale arrival can never
-- become what the user sees — that IS the event-time last-write-wins guarantee (no separate guard).

create table if not exists quotes (
    id         bigint generated always as identity primary key,
    symbol     text not null,
    price      double precision not null,
    volume     bigint not null,
    event_time timestamptz not null,   -- exchange/sample time; the ordering key
    fetched_at timestamptz not null default now(),
    source     text not null,          -- 'replay' | 'yahoo' | ...
    is_suspect boolean not null default false,  -- failed a sanity check; kept for audit, not shown as truth
    unique (symbol, event_time)         -- idempotent ingestion (safe re-fetch / at-least-once)
);

-- Latest-per-symbol reads and peak-excursion windows both scan by (symbol, event_time desc).
create index if not exists idx_quotes_symbol_time on quotes (symbol, event_time desc);
