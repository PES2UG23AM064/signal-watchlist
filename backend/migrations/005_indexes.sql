-- Retention prune deletes by event_time alone; without this index that query is a full scan that gets
-- slower exactly as the table grows (the one query that must not).
create index if not exists idx_quotes_event_time on quotes (event_time);

-- Auth lifecycle: tokens expire and can be revoked server-side; PIN attempts are throttled.
alter table users add column if not exists token_issued_at timestamptz not null default now();
alter table users add column if not exists failed_pin_attempts integer not null default 0;
alter table users add column if not exists locked_until timestamptz;
