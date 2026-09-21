# Cluster service access catalog

This catalog is the durable policy counterpart to the live
`get_platform_service_catalog`, `get_platform_permission_coverage` and
`get_platform_access_posture` tools. The live
tools discover every active namespace; this file defines the policy inherited by
each service domain. Kubernetes RBAC and NetworkPolicy are only the outer layer:
the native application authorization named below remains authoritative.

| Service domain | Plane | Agent observe | Agent operate | Approval-gated admin | Native authority | Human tracking |
| --- | --- | --- | --- | --- | --- | --- |
| Kubernetes/Cilium/Traefik | control | status, topology, events, fixed metrics | named tenant workload restart | reviewed RBAC/network/controller change | Kubernetes RBAC and admission | Headlamp, Hubble, Grafana |
| ToolHive/MCP Registry | control | approved catalog, proxy health, audit metrics | promotion request | stage transition for three named publications | ToolHive CR RBAC and Registry policy | Registry, Grafana |
| Codex/OpenSandbox | control/runtime | Agent/runtime status, no session credentials | named runtime recycle | identity/runtime policy later | Codex/OpenSandbox APIs and K8s RBAC | Codex console, Headlamp, Grafana |
| LiteLLM/AI gateway | control/data | gateway health and usage aggregates later | model route canary later | provider/key/budget policy later | LiteLLM teams, keys and budgets | LiteLLM Admin, Grafana |
| Authentik/Dex | control | issuer/provider health, user names only for admin | none | approval-bound identity and namespace grant | Authentik/Dex groups and Kubernetes OIDC RBAC | Authentik, Headlamp |
| Harbor/image publishing | control/data | health, project/image metadata | scan/promotion later | tenant/project/robot reconciliation | Harbor project roles and robot scope | Harbor, Grafana |
| Pigsty/PostgreSQL/PostgREST | data/routing | fixed route health and tenant metrics | approved migration job later | role/database/Secret plan and reconciliation | PostgreSQL roles, database grants and K8s Secret RBAC | CloudBeaver, Pigsty, Grafana |
| Supabase | data/control | workload/API health and schema metadata later | reviewed migration/deploy later | project auth/storage policy later | Supabase/PostgreSQL/Kong roles | Supabase Studio, CloudBeaver, Grafana |
| Neo4j | data | database health and graph metadata later | reviewed Cypher job later | role/database/user policy later | Neo4j native RBAC | Neo4j Browser, Grafana |
| Nuclio applications | control/data | function/gateway inventory and metrics | named function rollout later | project/gateway policy later | Nuclio CR RBAC and application auth | Nuclio Dashboard, Grafana |
| Fleet/device control | control | device/service health later | approved campaign later | enrollment and policy administration later | Fleet teams/roles | Fleet UI, Grafana |
| Observability/OpenCost/Robusta | control/data | dashboards, fixed tenant metrics and findings | alert acknowledgement later | datasource/alert/routing policy later | Grafana org/SA, tenant metrics proxy and product RBAC | Grafana, OpenCost, Robusta |
| Business application namespaces | data | workload, Service, Ingress and bounded metrics | named namespace rollout/restart | application-specific administration only | namespace RBAC plus application roles | Headlamp, Grafana and application GUI |

## Enforcement contract

Every published capability must pass all of these layers:

1. Separate human and Agent principal identities.
2. ToolHive allowlist and risk-specific observe/operate/admin surface.
3. Downstream native authorization scoped to named resources.
4. Approval ID for destructive or privilege-changing operations.
5. Audit event, OTLP/Prometheus telemetry, and a human GUI evidence path.
6. No credential-returning or generic Kubernetes/SQL proxy on a default surface.

## Progressive hardening contract

The access-posture findings are not applied blindly. A namespace must first be
labelled with an owner and service domain, then have its workload ServiceAccounts
audited before default token automount is disabled. NetworkPolicy starts in an
owner-reviewed allowlist/canary phase. NodePort and Ingress removal requires a
verified replacement route. This avoids breaking controllers or legacy workloads
while still making every exception visible and reviewable.

Current live baseline on 2026-08-04: all 37 active non-bookkeeping namespaces
have an explicit service domain, data/control-plane assignment, Agent permission
boundary, native downstream authority and human GUI evidence path. The live
inventory currently contains 169 workloads, 166 Services,
22 unique Ingress hosts; 19 namespaces lack a RoleBinding, 32 lack NetworkPolicy,
37 inherit default ServiceAccount token automount, and 14 expose at least one
NodePort/LoadBalancer Service. Re-run the ToolHive posture tool rather than
treating these counts as static inventory.

The verifier first resolves namespace configuration authority. Static platform
namespaces use `re8ch.com/config-authority=gitops` and must be present in the
explicit namespace-domain policy map. Namespaces carrying
`tenancy.re8ch.com/managed=true` and
`tenancy.re8ch.com/managed-by=tenant-control-controller` are dynamically owned
by the tenant controller: Git governs their boundary template, not each tenant
workload. Other controller-owned namespaces are evaluated against controller
health. Only genuinely unclassified namespaces fail the ownership gate.

The same classification is materialized on every active Namespace with
`re8ch.com/domain`, `re8ch.com/plane` and
`re8ch.com/access-policy=toolhive-v1`. This makes the decision directly visible
in Headlamp and usable by admission/policy selectors. The ToolHive apply flow
reconciles the labels, and both the live catalog and verifier fail when a label
drifts from the checked-in domain map.

The live per-resource permission matrix currently contains 335 decisions: 169
workloads and 166 Services. Nine Deployments map to exact approval-gated admin
restart/scale targets; declared tenant Deployments map to the tenant operate
boundary. Every other mutation is explicitly unpublished and remains behind its
native control plane. The shared ToolHive publications expose the reviewed
observe and operate schemas directly; the retired KAgent bridge is absent.

Workload catalog entries include desired, ready and available replica counts plus
an explicit `scaledToZero` flag. This is required because a Service without an
Endpoint may be intentionally stopped (currently Authentik and Nuclio Dashboard),
whereas a Ready backend blocked from ToolHive (currently OpenSandbox) is an
authorization/network-path result rather than an application availability result.
