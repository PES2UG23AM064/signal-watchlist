-- Exposure weighting: how much of a symbol the user holds. Nullable; unset means rank by unusualness alone.
alter table watchlist_items add column if not exists quantity double precision;
