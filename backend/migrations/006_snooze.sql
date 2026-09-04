-- Snooze: the inbox model's missing verb. A snoozed symbol stays in the list (nothing is hidden) but is
-- held out of "needs your attention" until the time passes.
alter table watchlist_items add column if not exists snoozed_until timestamptz;
