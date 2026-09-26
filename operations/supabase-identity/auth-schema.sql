-- Run as supabase_auth_admin against re8ch_identity before GoTrue starts.
-- GoTrue v2.189.0 expects this schema to exist during its first migration.
create schema if not exists auth;
