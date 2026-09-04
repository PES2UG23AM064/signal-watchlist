-- When WE received a quote, as opposed to event_time (the exchange/sample time it claims). The two differ
-- exactly when it matters: a future-dated bad tick has an event_time an hour ahead, so any "rejected in the
-- last 5 minutes" window keyed on event_time would count it as recent until the clock caught up. The
-- quarantine counters key on this column. (008 dropped the old `fetched_at`; this is its replacement with a
-- reader, and it defaults so no writer needs to change.)
alter table quotes add column if not exists received_at timestamptz not null default now();

create index if not exists idx_quotes_suspect_received on quotes (symbol, received_at desc) where is_suspect;
