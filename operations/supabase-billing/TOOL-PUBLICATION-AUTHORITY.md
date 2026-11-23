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

`accept_usage_event` accepts immutable, unpriced `usage.re8ch.com/v1` events.
The producer supplies a canonical payload SHA-256. Replaying the same
`(producer_ref, idempotency_key)` and digest succeeds; reusing the key for a
different event or digest fails. Pricing and accounting flags are constrained
to false so later billing stages remain separate authorities.

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
