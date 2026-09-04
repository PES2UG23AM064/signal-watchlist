-- Exposure weighting: optionally record how much of a symbol the user holds. Groww is a broker —
-- attention is move x what you hold, not the move alone. Nullable: unset means "rank by unusualness".
alter table watchlist_items add column if not exists quantity double precision;
