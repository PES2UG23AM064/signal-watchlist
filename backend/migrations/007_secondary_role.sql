-- Cross-source reconciliation: secondary-feed quotes are stored for cross-checking but never served.
-- Reads take the latest primary quote; a divergent secondary within the window marks the symbol "disputed".
alter table quotes add column if not exists role text not null default 'primary';
create index if not exists idx_quotes_symbol_role_time on quotes (symbol, role, event_time desc);
