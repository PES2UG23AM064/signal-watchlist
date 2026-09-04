-- When we received a quote, as opposed to the event_time it claims. The quarantine counters key on this:
-- a future-dated bad tick would otherwise count as "recent" under an event_time window until the clock
-- caught up. Defaults so no writer needs to change.
alter table quotes add column if not exists received_at timestamptz not null default now();

create index if not exists idx_quotes_suspect_received on quotes (symbol, received_at desc) where is_suspect;
