-- Quotes time-series: the shared poller writes one row per (symbol, poll); reads serve the latest by
-- event_time, so an out-of-order or stale arrival can never become the served price.

create table if not exists quotes (
    id         bigint generated always as identity primary key,
    symbol     text not null,
    price      double precision not null,
    volume     bigint not null,
    event_time timestamptz not null,   -- exchange/sample time; the ordering key
    fetched_at timestamptz not null default now(),
    source     text not null,          -- 'replay' | 'yahoo' | ...
    is_suspect boolean not null default false,  -- failed a sanity check; kept for audit, never served
    unique (symbol, event_time)         -- idempotent ingestion (at-least-once delivery is safe)
);

-- Latest-per-symbol reads and peak-excursion windows both scan by (symbol, event_time desc).
create index if not exists idx_quotes_symbol_time on quotes (symbol, event_time desc);
