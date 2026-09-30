-- Run as postgres against re8ch_identity before starting Realtime.
-- Realtime owns its internal migrations; only create the metadata schema here.
create schema if not exists _realtime;
alter schema _realtime owner to postgres;
