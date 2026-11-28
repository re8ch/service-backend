# Dynamic MCP Catalog authority

This contract separates tenant-controlled catalog data from platform delivery.
It applies to every MCP server and tool published through
`/tenant-native/mcp`.

## Authority

| State | Authority | Delivery rule |
| --- | --- | --- |
| User, organization, OAuth client and login session | PG Identity and OpenOAuth | Catalog rows reference stable subjects, organizations and opaque client references. They never contain client secrets. |
| MCP server ownership, tool schema, immutable revision, audience, grant, price and publication state | PostgreSQL Catalog | A database transaction changes the active revision and appends its projection event. A per-tool Git change is forbidden. |
| Dynamic ToolHive membership | Catalog projector | The projector consumes committed outbox events and converges the named MCP server entry and virtual-server membership idempotently. |
| Pod, Service, Endpoint and workload health | Kubernetes | Catalog publication is not readiness evidence. ToolHive must retain the last healthy projection when a candidate endpoint fails validation. |
| Platform deployment, database migrations, network and cluster policy, protected static tools | GitHub reviewed desired state, delivered by the bounded cloud SSA executor | Tenant-driven mutation of this lane is forbidden. |
| Invocation usage and settlement | Metering event store and billing ledger | Every event binds the caller, active organization, owner, tool and immutable revision. |

PG Identity and Catalog may share the approved PostgreSQL platform, but their
schemas and write roles remain separate. Identity deletion or organization
archival must make publication and invocation fail closed; it must not cascade
away billing or audit evidence.

## Publication transaction

The Catalog transaction that activates a revision must:

1. verify the authenticated subject's active organization and ownership;
2. validate the MCP protected-resource metadata, endpoint and exact tool schema;
3. store an immutable schema digest and opaque credential reference;
4. supersede the previous active revision with compare-and-swap semantics; and
5. append one ordered outbox event with an idempotency key.

The transaction does not write Kubernetes objects or Git. A projector claims
outbox events, applies the desired ToolHive projection, reads back effective
state and records the observed generation. Duplicate delivery is a no-op.
Out-of-order or stale revisions are rejected. Removal is a revisioned tombstone,
not an untracked delete.

The projector must not copy OAuth credentials into ToolHive or Catalog. It
references an approved server-side binding. A failed projection leaves the
event retryable and the last valid publication serving. A successful add,
change or removal must reach stateful MCP clients through
`notifications/tools/list_changed`.

## Dynamic and static lanes

Dynamic tenant tools and protected static tools share the public ToolHive
endpoint but never share authority:

- A tenant or organization owner may register a service MCP only in the dynamic
  lane. Publication is bounded by ownership, audience, grants, validation and
  metering policy.
- Governance, PostgreSQL administration and decisive cluster-control tools use
  the protected Admin lane. They remain reviewed platform declarations and
  require verified `admin` organization membership.
- Ordinary TenantGrant state cannot reveal an Admin tool. Admin membership
  cannot be inferred from membership in the `re8ch` organization.
- Database administration starts with bounded read-only inventory. Mutations
  require a preview, approval identifier, exact plan hash and idempotency key.
- Static tools may call the bounded cloud SSA executor. Dynamic registration
  must never acquire Kubernetes write authority merely because it is visible
  through the same VirtualMCPServer.

## Execution compatibility

Catalog is the dynamic control-plane authority, not an execution engine. A
revision selects a real MCP endpoint or an approved compatibility backend.
The existing `tenant-api`/`tenant-mcp` workload may host inherited adapters
while they are extracted, but it does not own publication, grants, ownership or
pricing. New tools do not require changes to that image when an independently
addressable MCP service already implements them.

The 893-operation inventory remains a discovery record. Only a validated,
active immutable revision is callable. Existing implementation and deployment
evidence is inherited by exact operation identity and schema digest; display
names are not identity.

## Recovery and audit

Catalog, projector and metering operations share a correlation identifier but
commit independently. Audit records contain subjects and opaque references,
never tokens, private keys, database passwords or Kubernetes Secrets. Git may
retain signed schema migrations, policy, release references and periodic
redacted snapshots. A snapshot is recovery evidence and never overrides newer
committed Catalog state.
