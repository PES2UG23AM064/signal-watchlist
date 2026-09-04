-- fetched_at was written but never read; freshness and ordering use event_time. (011 adds received_at with a reader.)
alter table quotes drop column if exists fetched_at;
