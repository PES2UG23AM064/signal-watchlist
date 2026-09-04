-- Cross-source reconciliation. A SECONDARY feed's quotes are stored for cross-checking but are NEVER the
-- served price: reads take the latest PRIMARY quote; a divergent secondary within the window marks the
-- symbol "disputed" (and shows the other price) instead of silently picking one.
alter table quotes add column if not exists role text not null default 'primary';
create index if not exists idx_quotes_symbol_role_time on quotes (symbol, role, event_time desc);
