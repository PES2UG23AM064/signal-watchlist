-- One session PER DEVICE, not one token per user. Before this, users.session_token was a single slot:
-- signing in on a phone silently signed the laptop out — the opposite of "identity that follows you
-- across devices". Tokens are stored hashed (a leaked table is not a leaked login).
create table if not exists sessions (
    token_hash   text primary key,
    user_id      uuid not null references users(id) on delete cascade,
    issued_at    timestamptz not null default now(),
    last_seen_at timestamptz not null default now()
);

create index if not exists idx_sessions_user on sessions (user_id);

-- The legacy single-slot column stays for older rows but is no longer required or read.
alter table users alter column session_token drop not null;
