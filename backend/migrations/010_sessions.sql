-- One session per device instead of a single users.session_token slot (which signed the other device out
-- on every sign-in). Tokens are stored hashed so a leaked table is not a leaked login.
create table if not exists sessions (
    token_hash   text primary key,
    user_id      uuid not null references users(id) on delete cascade,
    issued_at    timestamptz not null default now(),
    last_seen_at timestamptz not null default now()
);

create index if not exists idx_sessions_user on sessions (user_id);

-- The legacy single-slot column stays for older rows but is no longer required or read.
alter table users alter column session_token drop not null;
