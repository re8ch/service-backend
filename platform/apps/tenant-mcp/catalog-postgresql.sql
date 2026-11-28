-- PostgreSQL is the production authority for dynamic MCP publication state.
-- Revisions and the outbox record are committed in one transaction; consumers
-- order exclusively by sequence and may replay an event by event_id.
CREATE TABLE IF NOT EXISTS mcp_catalog_revisions (
  revision text PRIMARY KEY,
  tenant_ref text NOT NULL,
  service_name text NOT NULL,
  definition_json jsonb NOT NULL,
  state text NOT NULL CHECK (state IN ('active', 'paused')),
  previous_revision text REFERENCES mcp_catalog_revisions(revision),
  operation_key text NOT NULL,
  actor text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_ref, operation_key)
);

CREATE TABLE IF NOT EXISTS mcp_catalog_heads (
  tenant_ref text NOT NULL,
  service_name text NOT NULL,
  revision text NOT NULL REFERENCES mcp_catalog_revisions(revision),
  PRIMARY KEY (tenant_ref, service_name)
);

CREATE TABLE IF NOT EXISTS mcp_catalog_outbox (
  sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id text NOT NULL UNIQUE,
  tenant_ref text NOT NULL,
  service_name text NOT NULL,
  revision text NOT NULL REFERENCES mcp_catalog_revisions(revision),
  event_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mcp_catalog_outbox_tenant_sequence
  ON mcp_catalog_outbox (tenant_ref, sequence);
