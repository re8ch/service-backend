# Admin BYOC tool migration

## Decision

The Re8ch plugin has one public MCP resource: `re8ch-tenant`. A verified Re8ch
Admin organization session sees the platform cluster as an Admin-owned BYOC
cluster. It does not discover or invoke tools through `platform_tool_catalog`
or `platform_tool_call`, and it does not require separate public connections to
`re8ch-cloud-platform-tool`, `re8ch-cloud-observe`, or
`re8ch-platform-operate`.

The platform cluster registration, connection and Admin namespace are desired
state. The registration must resolve to the Admin organization and a Ready
connection before any cluster operation is advertised as available. Runtime
credentials remain server-side and are never returned by the plugin.

## Capability placement

| Existing capability | New tool surface | Scope |
| --- | --- | --- |
| Namespaced Kubernetes read/apply/delete | `tenant_kubernetes_get`, `tenant_kubernetes_apply`, `tenant_kubernetes_delete` | Admin-owned Ready namespaces on the selected Admin BYOC cluster |
| Secrets | `tenant_secret_upsert`, `tenant_secret_metadata_list`, `tenant_secret_delete` | Write-only/metadata-only in Admin-owned Ready namespaces |
| Workloads, storage claims and HTTPS exposure | Existing tenant claim, Kubernetes and exposure tools | Admin-owned Ready namespaces |
| Metrics, logs and dashboards | Existing tenant observability tools | Selected Admin BYOC cluster and authorized namespaces |
| Node inventory and reachability | Explicit `admin_cluster_node_list`, `admin_cluster_node_get`, `admin_cluster_node_reachability` | Cluster-scoped, read-only |
| Controlled node command | Explicit `admin_cluster_node_exec` | Cluster-scoped, exact argv, audited |
| Cluster-scoped Kubernetes objects and CRDs | Explicit `admin_cluster_resource_get`, `admin_cluster_resource_apply`, `admin_cluster_resource_delete` | Allowlisted GVR/GVK; plan-before-write; audited |
| Kube-apiserver authentication reconciliation | `reconcile_control_plane_authentication_config` | Reviewed Git commit and plan hash |
| Identity organization and user administration | `platform_organization`, `platform_user` | Re8ch Admin identity authority, not Kubernetes |
| Workspace indexing/sync | GitHub Actions and workspace controller | Not a runtime plugin tool |

The existing tenant Kubernetes allowlist remains the default safety boundary.
Admin organization membership does not silently turn tenant tools into an
unbounded `kubectl` shell. Cluster-scoped exceptions are separate tools so that
their schemas, review cases and audit policies remain visible.

## BYOC selection contract

Cluster-aware tools accept an opaque `clusterRef`. If it is omitted, the
server may select the sole Ready Admin-owned connection; it must fail with an
explicit ambiguity error when more than one Ready cluster exists. A prompt
supplied tenant name, cluster UID, issuer or namespace cannot override the
authenticated organization or grant.

The current BYOC registration ledger is registration metadata only. Before
the migration is declared complete, execution must route through the selected
Ready connection rather than the tenant MCP pod's local kubeconfig.

## Retirement gates

The three old public MCP publications may be retired only when all of these are
true:

1. Admin desired state contains a Ready namespace and Ready BYOC connection.
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
