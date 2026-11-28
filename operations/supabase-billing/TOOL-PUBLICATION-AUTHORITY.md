# Bifurcate tool publication authority

`re8ch_billing.bifurcate` is the runtime authority for self-registered tool
publications. It is business and metering state, not GitOps desired state. A
publication and every installation are owned by an organization. A Personal
Organization is an ordinary organization reference; there is no user-owned
exception or second ownership model.

The durable chain is `ToolPublication -> ToolRevision -> ToolAudience ->
ToolInstallation -> ToolBinding`. Revisions are immutable. Audiences are
explicit organization rows: `*`, implicit public visibility, and tenant-name
fallbacks are invalid. An admin audience starts pending and can become active
only when a different approving subject and approval timestamp are recorded.
The runtime must verify both subjects and current organization membership with
pg-identity before writing, but pg-billing stores only their canonical UUIDs.
It does not mirror users, memberships, OAuth applications, clients, grants,
tokens, sessions, or credentials.

Provider-backed tools are callable only through `ready_tool_bindings`. The view
fails closed unless publication, revision, audience, installation, and binding
are all simultaneously active. Binding rows contain opaque service and binding
references, never returned credential material.

Published revision definitions must carry `toolRef`, `providerAudience`, and
`allowedScopes`. `bifurcate_api.resolve_tool_invocation` exposes only the
ready binding decision to the authenticated tenant-control workload. The
runtime bearer for that PostgREST role is minted in-cluster as
`tenant-invocation-authority/BINDING_AUTHORITY_TOKEN`; absence is an explicit
fail-closed deployment gate, not a reason to accept caller-supplied authority.
The credential rotator runs beside the source Secret in `supabase-billing`;
only its two resourceNames-scoped output updates cross into
`tenant-infrastructure`. Empty Git declarations own metadata, not Secret data,
and must never be deployed with Helm force/replacement semantics.

`accept_usage_event` accepts immutable, unpriced `usage.re8ch.com/v1` events.
The producer supplies a canonical payload SHA-256 for audit correlation, but
the database does not trust it as replay proof. It compares every normalized
business field on conflict. Replaying the same `(producer_ref,
idempotency_key)` payload succeeds; changing any field or digest fails. Pricing
and accounting flags are constrained to false so later billing stages remain
separate authorities.

## Application and rollback

Plan and apply `tool-publication-authority.sql` to `re8ch_billing` only through
the reviewed `postgres_admin_sql` workflow after the Billing ownership repair.
The migration is additive and must be tested in a disposable database first.
No production application is part of this change. Rollback disables runtime
writes and grants; it must not drop publications, installations, bindings, or
usage events.

The existing tenant-mcp SQLite catalog and its `*` audience are legacy
compatibility surfaces. They must not be treated as this authority or copied
into these tables without an explicit organization audience migration.

## Dynamic MCP Catalog revisions

`dynamic-mcp-catalog-authority.sql` adds the server-level aggregate used to
project many tool definitions into one ToolHive instance. A server owns tools
and immutable revisions; a sealed revision contains the exact tool definitions,
input schemas, and SHA-256 values. `active_mcp_catalog` exposes only tools from
the atomically selected active revision. Catalog refresh consumers therefore
never combine rows from two revisions.

Subject UUIDs and organization references are canonical pg-identity/Supabase
references, not copied identity records. The caller must resolve the active
subject, organization membership, and Bifurcate grant before invoking an RPC.
The database enforces aggregate ownership and separation of duties: the
requesting subject cannot approve or publish its own registration. Registration
transitions are compare-and-set, publication requires a sealed revision, and
activation requires a published registration and the expected current revision.
Registration stores only the opaque OpenOAuth client reference used for later
binding; client secrets, grants, tokens, and sessions remain in OpenOAuth.

Every accepted registration transition and activation writes a transactional
outbox event in the same transaction. A projector may publish pending events
and mark them delivered, while replay remains safe through stable
deduplication keys. The active revision row is the rollback pointer: activate a
previously published sealed revision rather than mutating or deleting history.

GitOps packages only the versioned database migration, baseline runtime role,
and platform wiring. Individual servers, tools, revisions, registrations, and
active-revision selections are database business state and must never become
per-tool Git objects. Production rollout and outbox delivery are intentionally
outside this migration; rollback first disables runtime RPC access and restores
the previous active revision without dropping catalog history.
