-- The retention prune deletes by event_time alone; without this index it is a full scan that grows with the table.
create index if not exists idx_quotes_event_time on quotes (event_time);

-- Auth lifecycle: token issue time, throttled sign-in attempts, lockout.
-- (009 later renames failed_pin_attempts; this "add column if not exists" re-adds it on every startup.)
alter table users add column if not exists token_issued_at timestamptz not null default now();
alter table users add column if not exists failed_pin_attempts integer not null default 0;
alter table users add column if not exists locked_until timestamptz;
