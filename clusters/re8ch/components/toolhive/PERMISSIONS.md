# Platform tool permission model

Every capability has four independent controls: principal identity, ToolHive
tool visibility, downstream native authorization, and an observable human GUI.
A tool is publishable only when all four are defined.

| Domain | Observe | Operate | Admin | Downstream authority | Human evidence |
| --- | --- | --- | --- | --- | --- |
| Kubernetes | list/get approved resources | bounded restart in tenant namespace | reviewed RBAC/identity provisioning | Kubernetes RBAC | Headlamp + audit/Grafana |
| Metrics/logs | tenant summaries, fixed service health and dashboard links | same | datasource/config administration later | Grafana org/SA and metrics proxy | Grafana |
| Harbor registry | internal API health and project/image metadata | promotion/scan action later | tenant/project/robot provisioning | Harbor project roles | Harbor UI + Grafana |
| Pigsty PostgreSQL | fixed route health and tenant database metrics | migration job by approval later | role/database/Secret plan and reconciliation | PostgreSQL roles and K8s Secret RBAC | CloudBeaver + Pigsty/Grafana |
| Tool registry | API health and approved catalog metadata | request promotion later | approval-ID-gated canary/internal/deprecated transition for three reviewed servers | scoped ToolHive CR RBAC + Registry discovery | Registry API/CR status + Grafana |

## Implemented identities

| Level | Agent caller | Tool backend | Human GUI | Native scope |
| --- | --- | --- | --- | --- |
| observe | authenticated ConnecTool principal | `toolhive-system/ops-mcp-public` | `headlamp/platform-observer` | non-Secret get/list/watch |
| operate | authenticated ConnecTool operator | `toolhive-system/ops-mcp-operate` | `headlamp/platform-operator` | exact reviewed Deployment patch |
| admin | `toolhive-system/platform-admin-agent` | `toolhive-system/ops-mcp-admin` | existing separately controlled admin UIs | tenancy/RBAC/Harbor provisioning |

ConnecTool authenticates callers through Dex and applies its publication policy
before the dedicated ToolHive backend identities reach Kubernetes RBAC.

## Non-negotiable rules

- `secrets`, decoded credentials, kubeconfigs and login passwords are never
  observe tools.
- Generic Kubernetes and SQL proxies are not included in any default surface.
  A caller-supplied token does not make a generic proxy low risk.
- Write tools must be resource-scoped and idempotent. Destructive changes need
  an approval ID and must emit an audit event before execution.
- Humans and agents use separate identities even when they have equivalent
  roles. Human GUI passwords/tokens are never returned to agents.
- ToolHive controls publication; Kubernetes, PostgreSQL, Harbor and Grafana
  remain the final enforcement points for their own resources.

## Acceptance matrix

For each surface, call `tools/list`, then test one allowed and one denied action.

| Surface | Must be present | Must be absent |
| --- | --- | --- |
| observe | `list_k8s_resources`, `get_tenant_metrics_summary`, `get_platform_service_health` | `restart_deployment`, `get_kubeconfig`, every credential/admin tool |
| operate | `restart_deployment` | `k8s_api_proxy`, credential tools, every admin tool |
| admin | named provisioning tools in the checked-in allowlist | `k8s_api_proxy`, `postgres_sql_proxy`, credential-returning tools |

Also verify:

1. An unauthenticated request receives 401/403.
2. An observe identity cannot use the operate/admin audience.
3. Restart is rejected outside the authenticated tenant namespace.
4. ToolHive proxy logs include principal, server, tool, result and correlation ID.
5. Grafana exposes proxy health/error/latency; Headlamp shows the generated
   resources without granting viewers Secret read access.

Verified on 2026-08-04: observe exposes 25 tools, operate 26, admin 38; all three
reject unauthenticated calls, the generic/raw-credential tools are absent,
the shared ToolHive publications are Accepted, all three VictoriaMetrics targets
are `up=1`, and the Grafana dashboard API returns the expected dashboard UID. An
authenticated observe call reached 11 of 13 reviewed platform endpoints;
Harbor, CloudBeaver, Grafana, Headlamp and ToolHive Registry returned HTTP 200.
PgBouncer `pgwrite.zt.re8ch.com:6432` and the Shanghai read service
`pgread-sh.zt.re8ch.com:5434` refused connections and remain active topology
findings, not suppressed probe failures.

The admin publication tool was also called with an idempotent `canary`
transition and an approval ID. Its backend identity can patch the three named
MCPServers, cannot patch an unreviewed MCPServer, and cannot delete even an
approved MCPServer. Registry discovery still contains exactly those three
managed entries after the transition. The proxy audit event records
`platform-admin-agent`, tool name `set_tool_publication_stage`, and successful
outcome. The OTLP collector received traces, VictoriaMetrics recorded the
admin `tools/call`, and all three `toolhive-mcp-control-plane` targets are
currently `up=1`.

The deployed `tools/list` responses also carry client-visible safety hints:
all observe tools are `readOnlyHint=true`, all three surfaces are
`openWorldHint=false`, write tools are not marked read-only, and the publication
stage transition is `destructiveHint=true` because it can deprecate a tool.

`get_platform_service_catalog` complements the fixed endpoint probe with live
Kubernetes API inventory. It covers every active non-bookkeeping namespace and
returns workload, Service and Ingress names, control/data-plane classification,
default Agent boundaries, and the human GUI for evidence. It never reads Secret
objects or returns credentials. The verifier requires at least 35 namespaces,
every active namespace explicitly classified, 100 Services, and the named auth,
artifact, Agent, model gateway, database and ToolHive domains.

`get_platform_permission_coverage` turns that inventory into an effective
per-resource decision. Every live Deployment, StatefulSet, DaemonSet and Service
receives explicit observe/operate/admin behavior, downstream native authority,
data/control-plane classification and human GUI evidence. Managed admin actions
are derived from the same exact nine-target allowlist used by the mutation tools;
all other mutations remain native-control-plane-only instead of silently gaining
a generic Kubernetes write capability. The verifier rejects unclassified
namespaces, missing GUI/authority mappings, missing resource decisions or any
unexpected exposure of Secret/environment/Pod-spec data.

Namespace classification is also Kubernetes-native rather than MCP-only. The
managed apply flow reconciles domain, control/data plane and policy-version
labels for every active non-bookkeeping Namespace, so Headlamp and policy
engines see the same model returned to Agents. A conflicting legacy
`toolhive-system` Namespace declaration was normalized to the same labels.

`get_platform_access_posture` reads only RBAC/ServiceAccount/network exposure
metadata and reports per-namespace review signals for missing RoleBindings,
NetworkPolicies, default ServiceAccount token hardening, NodePorts and Ingresses.
These are triage signals rather than automatic vulnerability claims; application
roles in Authentik, Harbor, PostgreSQL, Neo4j, Supabase, LiteLLM and other native
control planes remain authoritative.

The fixed health tool also covers Authentik, LiteLLM, OpenSandbox,
Fleet, Neo4j, Supabase Studio, Nuclio, OpenCost and the Codex session runtime.
Non-2xx responses below 500 are reported as reachable rather than healthy;
timeouts/refusals remain visible and are never silently removed from results.

`restart_managed_platform_deployment` is an admin-only controlled operation for
authentication providers, OpenSandbox, Fleet, Supabase, Nuclio, Codex and the
shared Grafana. It records actor and timestamp on the Pod template,
actor and timestamp on the Pod template, and is constrained again by namespaced
Roles with `resourceNames`. RBAC cannot patch an unreviewed deployment or the
same deployment name in an unapproved namespace.

The canary call used the scaled-to-zero `nuclio/nuclio-dashboard` target. It
preserved replicas at zero, stored approval ID
`toolhive-platform-restart-canary-2026-08-04`, and emitted a successful ToolHive
`mcp_tool_call` audit event for `platform-admin-agent`. The former ToolHive admin
binding to the broader legacy `ops-mcp-tenancy-admin` role was replaced by a
dedicated tenancy role without Deployment patch permission.

`scale_managed_platform_deployment` uses the same exact target and downstream
RBAC allowlists, requires an approval ID, and accepts only zero or one replica.
It records previous/requested replicas and scale approval metadata for controlled
stop/bring-up. It is marked destructive because scaling a live control plane to
zero interrupts service.

`get_managed_platform_operation_status` is available on every surface and shows
desired/ready/available replicas plus the last restart/scale approval metadata
for all reviewed targets. This is the Agent-readable counterpart to Headlamp and
Grafana human tracking.

`get_nuclio_function_status` inventories all NuclioFunction resources across
application namespaces and exposes native state, image, replica bounds, node
selection and invocation status. Environment variables, Secret references,
annotations and last-applied configuration are deliberately omitted. Nuclio
Dashboard, Headlamp and Grafana provide the human views.

`diagnose_nuclio_function` accepts one existing namespace/function and correlates
the sanitized Nuclio state with its Deployment, Pods and recent Events. It is
read-only on every surface and omits environment, Secret and annotation data.

`get_nuclio_capacity_status` is the rollout preflight: it reports Nuclio-labelled
node readiness, schedulability, taints and allocatable capacity, plus placed and
pending Nuclio Pods. A rollout should not proceed until its gate has a viable
matching node. Headlamp and Grafana provide the human capacity views.

`get_litellm_gateway_status` uses only LiteLLM's unauthenticated liveness and
readiness endpoints plus Deployment status. Model, provider, virtual key, team,
budget and full health APIs remain protected by LiteLLM native authorization.
Master/provider keys, database URL, UI credentials and ConfigMap contents are
never read. LiteLLM Admin and Grafana provide the human views.

`get_fleet_control_status` uses Fleet's public health/version endpoints and
Kubernetes workload readiness only. Host inventory, policies, queries,
activities and campaigns remain protected by Fleet native authorization. Fleet
API tokens, device data and MySQL/Redis credentials are never read. Fleet UI,
Headlamp and Grafana provide the human views.

`get_supabase_platform_status` uses Supabase Studio reachability and Kubernetes
Deployment/endpoint readiness only. Supabase JWT/RLS, PostgreSQL roles and each
component's native authorization remain authoritative. JWT keys, database URLs,
credentials, users, rows, buckets, objects and schema contents are never read.
Supabase Studio, CloudBeaver, Headlamp and Grafana provide the human views.

`get_neo4j_platform_status` reports Neo4j Browser reachability and sanitized
StatefulSet/endpoint readiness for all graph instances. Cypher/Bolt operations,
transactions and database/user/role administration remain subject to Neo4j
native roles and database privileges. Credentials, queries/results, graph data,
schema and configuration are never read. Neo4j Browser, Headlamp and Grafana
provide the human views.

`get_identity_platform_status` distinguishes deliberately scaled-to-zero
identity workloads from desired-but-unready workloads and reports only fixed
health probes plus Deployment/endpoint readiness. Authentik users, groups,
applications, providers, flows, policies, sessions, tokens and protocol secrets
remain under Authentik native authorization and are never read. Authentik,
Headlamp and Grafana provide the human views.

`get_agent_runtime_status` correlates sanitized OpenSandbox and Codex
Deployment readiness with RemoteMCP acceptance/tool counts and aggregate
OpenSandbox resource phases. Agent prompts, messages, memories, model/provider
configuration, credentials, sandbox names/specifications/files/processes/output
and Codex session contents are never read. Native runtime APIs and Kubernetes
RBAC remain authoritative; Codex, Headlamp and Grafana provide the human views.

`get_observability_platform_status` correlates fixed health probes and workload
readiness across Grafana, VictoriaMetrics, VMAlert, VictoriaLogs, OpenCost,
Robusta and the OTLP Collector. It runs only two fixed target-count queries and
one cluster-level one-hour OpenCost allocation aggregate. Arbitrary PromQL or
LogsQL, raw series labels, logs, traces, alert contents, tenant/workload costs,
Robusta findings and notification destinations are never read. Grafana tenant
organizations/service accounts, metrics/log proxies, OpenCost OAuth and native
product authorization remain authoritative; Grafana, OpenCost and Headlamp are
the human views.

`get_artifact_platform_status` separates Harbor core availability, scanner
readiness and deliberately scaled-to-zero image-control functions. It exposes
only Harbor component names/status and workload readiness. Robot credentials,
image-pull Secrets, manifests/layers, SBOMs, vulnerability details, users and
members are never read. Harbor project/repository roles, robot scopes and
scanner policy remain authoritative; Harbor, Headlamp and Grafana are the human
views.

`get_database_routing_status` distinguishes direct PostgreSQL, HA service,
PgBouncer and regional PostgREST paths. It returns fixed route reachability and
EndpointSlice counts without endpoint addresses, plus CloudBeaver reachability.
Arbitrary SQL, OpenAPI schema bodies, roles/grants, database contents,
credentials and CloudBeaver connections are never read. PostgreSQL roles/RLS,
PgBouncer authentication, PostgREST JWT/database roles, CloudBeaver roles and
Kubernetes Secret RBAC remain authoritative.

`get_external_publication_readiness` gives internal Agents the same fixed
DNS/TLS/OAuth/RFC 9728/MCP challenge and cluster-object gates as the shell
readiness checker. It accepts no arbitrary origin and reads no OAuth client
secret, token, certificate body, DNS API credential or user claim. The tool
keeps the absent external MCPServer and Ingress visible as blockers until the
separate public-read surface and all identity gates are proven.
