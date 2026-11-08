# Admin direct-cluster and tenant BYOC tool migration

## Decision

The Re8ch plugin has one public MCP resource: `re8ch-tenant`. A verified Re8ch
Admin organization session operates RE8CH-owned platform clusters through a
server-side direct connection. BYOC is reserved for other organizations that
bring an external cluster under control through AmongCluster. Admin does not
register the platform cluster as BYOC. The plugin does not discover or invoke tools through `platform_tool_catalog`
or `platform_tool_call`, and it does not require separate public connections to
`re8ch-cloud-platform-tool`, `re8ch-cloud-observe`, or
`re8ch-platform-operate`.

The direct platform-cluster connection and Admin authorization are desired
state. Runtime credentials remain server-side and are never returned by the
plugin. External BYOC registration must resolve to its owning organization and
a Ready AmongCluster connection before its operations are advertised.

The `admin` organization is the Meta tenant. A verified human Admin session is
not constrained by ordinary TenantGrant or Ready-namespace discovery when it
uses the explicit `platform_*` tools: those tools address every namespaced and
cluster-scoped resource through the direct native backend. Secret values remain
write-only or redacted. Ordinary `tenant_*` tools retain tenant isolation and
must not be relabeled as cluster-wide merely to make discovery appear healthy.

## Capability placement

| Existing capability | New tool surface | Scope |
| --- | --- | --- |
| Platform Kubernetes discovery/read | `platform_kubernetes_resource_read` | Directly connected RE8CH platform cluster; all API resource types discoverable; Secret values excluded |
| Platform Kubernetes apply/delete | `platform_kubernetes_resource_apply`, `platform_kubernetes_resource_delete` | Direct cluster; any namespaced or cluster-scoped resource; server dry-run and exact plan hash before write |
| Namespaced tenant Kubernetes read/apply/delete | `tenant_kubernetes_get`, `tenant_kubernetes_apply`, `tenant_kubernetes_delete` | Tenant-owned Ready namespaces, including external clusters selected through AmongCluster |
| Secrets | `tenant_secret_upsert`, `tenant_secret_metadata_list`, `tenant_secret_delete` | Write-only/metadata-only in Admin-owned Ready namespaces |
| Workloads, storage claims and HTTPS exposure | Existing tenant claim, Kubernetes and exposure tools | Admin-owned Ready namespaces |
| Metrics, logs and dashboards | Existing observability tools | Direct platform connection for Admin; selected AmongCluster connection and authorized namespaces for external tenants |
| Node inventory and reachability | `platform_nodes_list`, `platform_nodes_get`, `platform_nodes_reachability` | Cluster-scoped, read-only |
| Controlled node command | `platform_node_ssh_exec` | Cluster-scoped, exact argv, audited |
| Arbitrary Kubernetes commands | `platform_kubectl_exec` | Direct cluster, exact argv, bounded timeout, audited |
| Kube-apiserver authentication reconciliation | `reconcile_control_plane_authentication_config` | Reviewed Git commit and plan hash |
| Identity organization and user administration | `platform_organization`, `platform_user` | Re8ch Admin identity authority, not Kubernetes |
| Workspace indexing/sync | GitHub Actions and workspace controller | Not a runtime plugin tool |

The existing tenant Kubernetes allowlist remains the default safety boundary.
Admin organization membership does not silently turn tenant tools into an
unbounded `kubectl` shell. Cluster-scoped exceptions are separate tools so that
their schemas, review cases and audit policies remain visible.

## Cluster selection contract

Admin platform tools use the reviewed server-side direct platform connection;
they do not accept a caller-supplied cluster identity. External-cluster tools
accept an opaque `clusterRef` and require a Ready AmongCluster connection owned
by the authenticated organization. A prompt-supplied tenant name, cluster UID,
issuer or namespace cannot override the authenticated organization or grant.

The current BYOC registration ledger is registration metadata only. Before
external-cluster migration is declared complete, execution must route through
the selected Ready AmongCluster connection rather than the tenant MCP pod's
local kubeconfig. This does not apply to the Admin direct platform connection.

## Retirement gates

The three old public MCP publications may be retired only when all of these are
true:

1. Admin direct-cluster authentication is Ready; external BYOC cases have a
   Ready AmongCluster connection owned by their tenant.
2. The unified endpoint publishes the new explicit tools with concrete JSON
   schemas and no generic dispatch bridge.
3. Read parity, plan parity, write/audit parity and revocation tests pass for
   every capability retained from the old runtimes.
4. The OpenAI review ZIP contains only the unified tenant MCP connection and
   review cases exercise real Admin arguments.
5. Runtime verification proves that revoking Admin membership or the BYOC
   connection immediately denies new operations.

## Custom cloud tools

Dynamic skills are reviewed instructions, not executable connector
registrations. A future tenant-owned cloud connector must have a separately
reviewed definition, write-only credential reference, egress allowlist, input
JSON Schema, audit trail and publication lifecycle. On publication the MCP
server should expose it as a real named tool with that input schema (using
`tools/list_changed`), rather than recreate a generic `tool_call(arguments)`
bridge.
