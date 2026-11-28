# Dynamic Catalog projection authority

PostgreSQL Catalog owns immutable user MCP revisions, the active head and the
ordered transactional outbox. A registration, change, withdrawal or rollback
commits its revision, head and outbox record atomically. Consumers resume from
the monotonic outbox `sequence`; `event_id` and the operation key make replay
idempotent. A rollback creates a new active revision that refers to the chosen
historical definition. It never moves history backwards.

The catalog gateway consumes `/internal/mcp/catalog/events`, validates the full
revision, swaps its in-memory tool table atomically and retains the last valid
table when validation or an upstream connection fails. It publishes real tool
names and schemas and emits `notifications/tools/list_changed` after the swap.
Invocation is never retried because a failed response may follow a committed
write. Catalog stores only opaque OAuth application or binding references; it
does not store credentials.

ToolHive has one stable `MCPServerEntry` (`tenant-api-backend`) and one stable
`VirtualMCPServer` (`re8ch-native`). Those objects, static cluster-control tools,
placement, OIDC and network policy remain reviewed Git desired state delivered
by the bounded Cloud SSA executor. Dynamic tenant tools are data-plane Catalog
state: registration, revision changes and withdrawal do not create Kubernetes
objects and do not require a Git commit. A runtime process must never apply a per-tool ToolHive CRD.

The PostgreSQL schema contract is
`platform/apps/tenant-mcp/catalog-postgresql.sql`. SQLite tables in tenant-mcp
exist only as the local compatibility/test implementation of the same revision,
head and outbox semantics; they are not a second production authority.
