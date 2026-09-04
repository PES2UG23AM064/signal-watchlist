-- Snooze: a snoozed symbol stays in the list but is held out of "needs your attention" until the time passes.
alter table watchlist_items add column if not exists snoozed_until timestamptz;
