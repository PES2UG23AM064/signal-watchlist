-- fetched_at was written but never read anywhere (provenance uses event_time — the exchange/sample time,
-- which is the only timestamp that matters for freshness and ordering). Dead columns are review bait.
alter table quotes drop column if exists fetched_at;
