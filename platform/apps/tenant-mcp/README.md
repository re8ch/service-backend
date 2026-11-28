# Tenant MCP (Go)

This service replaces the `tenant-api` Python profile, including its MCP tools,
resources, OAuth token exchange, billing read endpoint, usage ingestion, and
one-time credential redemption and adoption pages. The `admin-api`, cloud,
infra, and orchestra profiles stay in the existing Python image.

The MCP server uses the official Go MCP SDK and the existing ToolHive tenant
vMCP publication. The token exchange preserves the caller's Supabase access
token; every tenant request checks it with GoTrue and resolves current tenant
membership through tenant-control. The ToolHive service credential supports
MCP discovery only. Tenant tool calls need a tenant member token.

`model_gateway_catalog` and `model_gateway_chat` provide optional bounded
LiteLLM inference for the authenticated organization. They require an active
model-gateway grant and a dedicated server-side tenant binding; configuration
does not prove provider readiness. See [activation and research usage](MODEL-GATEWAY.md).

Authentication and tenant authorization have separate outcomes. The identity
RPC idempotently creates or restores the verified user's Personal organization
owner membership before resolving a runtime. The identity database owns one
account-wide active organization; `tenant_organization_context_list` discovers
the available contexts and `tenant_organization_context_select` changes it.
`X-RE8CH-Tenant` is only a compatibility assertion and cannot select or grant a
tenant. A verified user that still cannot resolve one tenant can initialize MCP
and receives the status and organization-context tools, including a
machine-readable access state and recovery action; tenant resources and
operations remain unavailable.
An invalid token receives an OAuth challenge, while an unavailable tenant
authorizer returns 503. Supabase owns the user, Personal organization, and
membership. Tenant-control requires an active runtime Tenant policy. Ordinary
Personal organizations use the predeclared `personal-standard` policy, while
other organizations require their own reviewed runtime declaration.
Personal organizations without their own runtime Tenant map to the
`personal-standard` runtime class. Their verified organization reference remains
the resource owner. Each owner receives its own namespace and provider identity;
the shared class supplies policy and capacity. The complete tenant tool surface
is available to these users, subject to their Supabase role and resource state.
The mapping is documented in [SHARED-TENANT-MODEL.md](SHARED-TENANT-MODEL.md).

Tenant plans have two explicit resource paths. Cluster plans set
`nativeAccess` and, where appropriate, `byocAllowed`; their members may use the
tenant Kubernetes or BYOC tools. Platform-managed plans keep both flags false
and consume infrastructure through `tenant_claim_*`, `tenant_exposure_*`,
`service_instance_*`, and opaque `service_binding_*` references. Managed plans
still receive an internal owner namespace, but its existence does not grant the
member a general Kubernetes management surface.

Tenant owners and operators can manage allowlisted ConfigMaps, workloads and
ClusterIP Services through the native Kubernetes tools. Secret values use the
separate `tenant_secret_*` broker: values are write-only, while reads expose
only names, key names and Kubernetes metadata. Public HTTP access uses
`tenant_exposure_*`, which creates a constrained HTTPRoute to a same-namespace
ClusterIP Service. Hostnames are allocated below `apps.re8ch.com`; the shared
Gateway, listener, DNS and TLS policy remain platform-owned. Raw Secret and
Gateway access are not published.

## Recovery and cross-component acceptance

Use the [runtime verification procedure](../../../operations/releases/toolchain-runtime-verification.md)
for Admin/BYOC, deployment, registry, Sentry and Metrics acceptance. Current bounded
observations and remaining gaps are tracked in [Issue3002](https://github.com/re8ch/service-backend/issues/3002).
A successful tools/list or retry alone does not establish toolchain stability.

## Tool-call telemetry

The native tenant MCP records completed authenticated `tools/call` requests at
the SDK middleware boundary and exposes Prometheus metrics on `/metrics`:

- `re8ch_mcp_tool_calls_total` records the canonical `tool_name`, `outcome`, and
  a bounded `error_category` (`none`, `tool`, `protocol`, `timeout`, or
  `canceled`);
- `re8ch_mcp_tool_call_duration_seconds` records latency with the same labels.

The metrics never contain tenant, user, session, arguments, results, bearer
tokens, or free-form error text. Tool-name cardinality is capped; invalid or
overflow names are aggregated as `__unknown__`. The endpoint has no public
Gateway route and is reachable by the declared observability scraper through
the namespace NetworkPolicy.

Compatibility names are rewritten before MCP dispatch and are not included in
`tools/list`. Authenticated use of a compatibility name increments
`re8ch_mcp_tool_alias_calls_total` with only its bounded alias family and
canonical tool name. This permits a measured deprecation window: publish the
smaller canonical surface, retain old calls temporarily, and remove an alias
only after its usage reaches zero.

The monitoring product surface is provider-neutral and intentionally compact:
`monitoring_catalog`, `monitoring_query`, `monitoring_analyze`, and
`monitoring_dashboard`. Historical `grafana_*` calls remain hidden aliases for
the measured migration window; Grafana remains an implementation detail and
provider-specific resource URI until a separate resource migration is reviewed.

The runtime-image surface similarly publishes three product-oriented tools:
`runtime_image_catalog` for project, repository, artifact and security reads;
`runtime_image_manage` for scan and tag lifecycle; and `runtime_image_build` for
the integrated BuildKit/SBOM/provenance/signing/scan workflow. Historical
`harbor_*` calls remain hidden measured aliases. Harbor is still the current OCI
registry implementation, but it is no longer part of the public tool names.

## Dynamic skills

Dynamic skills are reviewed workflow text for the current Agent to interpret
with its existing tools. They do not execute arbitrary code or add new
privileges. `dynamic_skill_search` returns published matches for the current
tenant and platform-wide skills; `dynamic_skill_load` returns a selected
workflow. Tenant operators can submit a draft with `dynamic_skill_submit`.
Drafts remain invisible until an admin reviews them.
Set `visibility: personal` when submitting a workflow that should follow one
GoTrue user across computers. The server binds it to the authenticated subject;
search and load require the same subject and tenant after publication. The
default remains tenant visibility. A reviewable User Hub release supervision
workflow is in `workflows/userhub-pipeline.md`; publishing it for a person
requires that person to submit it from their authenticated session and an
administrator to approve the resulting draft.

The Python `admin-api` publishes four management tools while the Go tenant API
is enabled: `dynamic_skill_pending`, `dynamic_skill_publish`,
`dynamic_skill_review`, and `dynamic_skill_revoke`. They call the Go service's
separate port 19450, which requires the admin backend token and is reachable
only from `admin-api` under the chart NetworkPolicy. Skills are stored in
`dynamic-skills.db` on the existing tenant state volume. A new published
version revokes the previous version of the same name and tenant.

## Build and rollout

`tenant_credits_wallets_list` reads the signed-in user's authorized personal
and organization Credits balances through the existing Identity Credits Ledger
`api.credits_wallets()` RPC. The tenant MCP forwards the verified Supabase
bearer to the read-only identity PostgREST endpoint; the RPC applies wallet
authorization using `auth.uid()`. Results are Credits units, not currency or
unpriced usage totals.

The Re8ch plugin's public `/tenant-native/mcp` route maps to `/plugin/mcp`.
Platform-administration operations are published as explicit tools with
reviewable input schemas. Tenant-owned service instances additionally expose
the stable `service_instance_capabilities` and `service_instance_invoke`
contract: capabilities are discovered live from the provider selected by the
instance's service class, while invocation is bound server-side to the exact
tenant, subject, instance, Ready binding and granted scopes. This is a bounded
ServiceInstance dispatch path, not a platform-wide backend catalog, and it
never returns provider credentials or permits caller-selected origins.
Calls require a verified user with a current
`owner` or `admin` membership in the hidden `admin` organization. Listing an
entry point does not grant access to any backend tool.
`platform_kubectl_exec` exposes the native `kubectl.exec` operation directly
with an `argv` array and bounded timeout, while preserving the same platform
administrator gate, backend authorization, and audit result. Its action definition
is explicit so a plugin reviewer can assess it independently of the generic
backend operation. `platform_kubernetes_resource_read` discovers all API
resource types and reads bounded non-Secret objects.
`platform_cluster_bindings_list` lists the existing self cluster (`re8ch`) and
reviewed Admin-managed AmongCluster targets without registering duplicate BYOC
connections. The existing `byoc_connection_list` exposes these managed targets
and an exact `platform_kubectl_exec` example per target. Existing argv-only clients
select them with standard `--context=<clusterRef>` or `--context <clusterRef>`.
The core resolves and removes this selector before execution; it cannot select
a kubeconfig identity. Unknown, repeated, empty or conflicting selectors fail
before a business command. Other target and identity overrides remain forbidden.
This supports reads, dry-runs, SSA and updates without reconnecting a client whose
tool schema is cached.

Kubernetes read/apply/delete and kubectl tools also accept typed `clusterRef`;
omission preserves the existing local adapter. An unknown or withdrawn target
never falls back locally. Mutation plans bind the target and current reviewed
binding digest. Admin infrastructure access is independent of the ordinary
tenant BYOC registration policy.
`platform_kubernetes_resource_apply` and
`platform_kubernetes_resource_delete` provide server-validated previews and
require the exact preview plan hash before mutating any namespaced or
cluster-scoped object. `platform_nodes_list`,
`platform_nodes_get`, `platform_nodes_reachability`, and
`platform_node_ssh_exec` preserve the existing node operations through the
same managed platform binding model. These Admin infrastructure targets are
separate from ordinary tenant BYOC registrations and do not grant cross-tenant
application access. Publishing these explicit operations does not publish the remaining
backend tools. Plugin clients must use only operations
in their current tool list; an authorization flag cannot attest publication.
It reads the Supabase membership projection once per verified login session and
caches that decision in the tenant process.
A new login session receives a fresh decision; a process restart clears the
cache. The service bearer cannot use it. Each backend enforces its own
authorization; the gateway does not duplicate that authorization.
Backend credentials are read by the tenant runtime's Kubernetes
service account from three named Secrets via tightly scoped `get` RBAC and are
never returned to the caller.

The authenticated Re8ch plugin is the administrator entry for explicitly
published operations. An active organization session with verified
platform-admin membership is still subject to the current plugin tool list
and backend permissions. Empty tenant consumables or service classes do not
restrict this administrator authority. The internal `admin` backend publishes
`postgres_admin_sql` for reviewed Pigsty SQL:
read-only queries execute through the fixed Supabase database identity. The
gateway publishes this adapter as the protected `admin.database-governance.v1`
module only while the active organization is `admin`; membership in `re8ch`
does not cross that organization boundary. Call
`platform_database_governance_inventory` first. Writes bind that exact
inventory hash, a reviewed approval ID and an idempotency key into the preview
plan, and execute only with `apply=true` and that exact plan hash. Completed
identical retries replay the stored result; an interrupted pending operation
fails closed for audit inspection instead of executing again.
Neither callers nor tool results receive the database password. This path
avoids requiring a fresh Supabase Studio login for every migration.
It does not replace GitOps: deterministic cluster resources, database workload
configuration and other static control-plane changes continue through reviewed
Git and the bounded Cloud SSA executor.

The standalone pg-re8ch publication is a separate resource server at
`https://pg-re8ch-mcp.service.re8ch.com/mcp`. It reuses the same verified
PG Identity bearer and requires active `admin` organization platform-admin
membership. It never publishes the internal Supabase Studio address or its
credentials. `pg_re8ch_migration_preview` binds the fixed `pg-re8ch` project,
migration name and SQL SHA-256 into a plan hash. Only
`pg_re8ch_migration_apply` accepts that exact plan together with a reviewed
approval ID and idempotency key; raw `apply_migration` is not published.
`pg_re8ch_list_migrations`, `pg_re8ch_list_tables`, and
`pg_re8ch_execute_sql` provide post-apply readback using the upstream schemas,
but force the same project and reject mutating or multi-statement SQL. SQL and
credentials remain request/runtime data and never become Catalog or GitOps
records.

### Re8ch Admin GitHub Enterprise governance service

GitHub Enterprise governance is exposed as the provider-backed singleton
`service://admin/github-enterprise-governance`. It deliberately reuses
`service_instance_update`; it does not register another MCP tool and is not a
generic operation dispatcher. The catalog entry for
`integration.github.enterprise-governance.v1` publishes the following opaque
contract:

```json
{
  "entrypoint": "service_instance_update",
  "requestField": "spec",
  "planApply": true,
  "credentialCapabilities": [
    "governance", "delivery", "security", "collaboration",
    "developer-ai", "identity", "billing"
  ],
  "deniedOperations": ["delete"],
  "resources": [
    "repository_ruleset",
    "organization_ruleset",
    "repository_variable",
    "organization_variable",
    "organization",
    "workflow",
    "workflow_run"
  ]
}
```

Execution is cloud-to-cloud from the provider to the allowlisted GitHub REST
endpoint. Local checkouts, SSH proxies and Kubernetes writes are outside this
operation contract.

The caller must be an `admin` member in the hidden `admin` organization, the
`integration.github.enterprise-governance.v1` grant must be Active, and the
singleton must be Ready. The runtime selects `spec.capability` (default
`governance`), resolves that App installation for the requested `owner`, creates
a short-lived installation token, performs only
the operation pairs listed below, and returns an operation-specific
`github-governance://` audit reference. It never returns the private key or
installation token.

| `spec.resource` | Required selectors | Operations |
| --- | --- | --- |
| `repository_ruleset` | `owner`, `repo`; `id` for get/update | list/get/create/update |
| `organization_ruleset` | `owner`; `id` for get/update | list/get/create/update |
| `repository_variable` | `owner`, `repo`; `name` except for list | list/get/create/update |
| `organization_variable` | `owner`; `name` except for list | list/get/create/update |
| `organization` | `owner` | get/update |
| `workflow` | `owner`, `repo`, workflow file in `name`; `ref` and optional `inputs` in `body` | dispatch |
| `workflow_run` | `owner`, `repo`, run `id` | get/rerun/rerun_failed |

Organization updates are restricted to the provider allowlist: `name`,
`billing_email`, `company`, `blog`, `location`, `twitter_username`,
`description`, `has_organization_projects`, `has_repository_projects`,
`default_repository_permission`, `members_can_create_repositories`, and
`web_commit_signoff_required`. Ruleset and Variable request bodies follow the
corresponding GitHub REST API payloads, but the adapter selects the endpoint;
callers cannot supply a URL or HTTP method.

Every operation named `delete` is rejected before credential selection. This
is a shared service invariant for all seven Apps. Permissions that inherently
include repository deletion are not granted write access.

Reads omit `apply` and execute immediately. For a mutation, first submit the
intended `spec` without `apply`. The response contains `applied: false`, the
bounded method and path, `planHash`, and `credentialMaterialReturned: false`.
Submit the unchanged request with `apply: true` and that exact `planHash`, then
read the resource back from GitHub. A changed body, selector, or operation
invalidates the hash. Use a distinct outer `idempotency_key` for every logical
preview or apply request.

The 2026-11-10 production acceptance used the predecessor contract to list the
effective Rulesets for `re8ch/service-backend`, read the `re8ch` organization
settings, and exercise a non-secret repository Variable lifecycle. The current
contract rejects deletion for every capability. A successful read or write proves only the requested organization,
resource, and current GitHub App permissions. It does not prove access to
Secrets, arbitrary REST/GraphQL endpoints, ruleset bypass, or an organization
where the App is not installed.

Run `go test -race ./...` and `go vet ./...` in this directory. The repository
chart tests are `python3 -m pytest tests/test_tenant_infrastructure_gitops.py`.
Run `python3 differential_test.py` here to start both tenant runtimes against
the same fake identity services and copied SQLite fixture. It compares all
legacy tool schemas, selected read and write results, tenant resources, OAuth,
billing, authorization boundaries, and idempotent replay. Resource list order
is normalized because the Go SDK sorts the list by URI.
Build locally from the repository root with
`docker build -f platform/apps/tenant-mcp/Dockerfile -t re8ch-tenant-mcp:local .`.

The `tenant-go-release` GitHub workflow validates PRs. On `main`, a GitHub-hosted
runner publishes one amd64 GHCR image with provenance and SBOM, then attests its
immutable digest. Its scoped promotion PR updates
`clusters/re8ch/tenant-mcp-image/image.yaml`; the active observability
bundle reads that ConfigMap as a required input.
Verify the image digest, deployment readiness, tenant ToolHive canary, billing,
and credential redemption through the runtime before calling the release
complete. Rollback is a signed PR restoring the previous image digest.

The public HTTP routes and ToolHive backend URL do not change during cutover.
No credentials or submitted workflow records are committed to Git. Reviewed
workflow templates may be kept here as source for an explicit submission.

### Module configuration and lifecycle

`PLATFORM_MODULE_REGISTRY` points at a versioned JSON document. The chart mounts
`tenant-platform-modules` as a directory (no `subPath`) so Kubernetes projects
ConfigMap changes atomically. The gateway checks the file on requests, at most
once per second, validates the complete document, and swaps modules atomically.
Invalid or missing updates retain the previous valid registry. The embedded
`platform-modules.json` is the startup fallback; the chart copy must match it in CI.
Only GitOps administrators may change module URLs and credential references.
The currently registered tools from each backend are discovered at runtime;
the gateway does not maintain a second per-tool allowlist. Credentials never
appear in the registry.

Discovery runs concurrently with a five-second total budget per module,
including initialization, and caches successful or failed discovery for 30
seconds. MCP sessions are reused; credentials are refreshed after five minutes
when the session is idle. Changing/removing a module drains calls using its old
session. Failed calls are never automatically replayed; callers must consult
operation status before retrying a write. Tools are sorted for predictable
catalog responses. A disconnected module does not hide healthy modules.

SIGTERM/SIGINT drains both HTTP listeners for up to 60 seconds. This is groundwork
for rolling deployment, not a claim of multi-replica safety: the current chart
still uses one replica and Recreate while the SQLite state and session cache
are being migrated to shared storage.

### Shared login entitlements

After Cloud Functions migration `046_identity_platform_sessions.sql` and the
Auth Gateway `/v2/platform/session` release, set `PLATFORM_SESSION_URL` to that
internal endpoint. It replaces the direct GoTrue verification request and the
process-local platform membership cache. Auth owns session liveness and one
membership decision per login. No extra MFA challenge is required. Token refresh
and gateway replica changes preserve the decision; logout, revocation, and expiry
invalidate the session. If the identity authority is unavailable, MCP returns
503 without a login challenge. Leave the option unset until the additive Auth
migration and API deployment have been verified.

### Platform identity users

`platform_user` is advertised only to human platform-admin sessions when
`PLATFORM_USERS_URL` points to the identity gateway `/auth/v2/users`. It provides
`list`, `get`, `create`, `update` and `delete`, forwarding the current caller's
bearer with no retries or service-role credential. Creation is passwordless and
unverified; update changes the canonical display name. Delete is GoTrue soft
deletion, not personal-data erasure.

Deployment requires Cloud Functions PR #107 and SQL migrations 048 (auth-owner
RLS policy first). The v5 platform-admin migration Job embeds those migrations.
Deploy the auth gateway and tenant-control dependency endpoint before publishing
the tenant MCP image and User Hub PR #70.

The dependency endpoint reads Kubernetes authority and fails closed. Any retained
Tenant, namespace, binding, grant, service instance, or attributed persistent
volume blocks archival; remove runtime declarations through their normal lifecycle
before deleting the identity. Released status alone does not prove data is gone.
The preflight and GoTrue deletion are separate service calls, not a distributed
transaction.

### OpenAI public directory domain verification

After publishing a verified image containing `publication.go`, set
`tenantPlatform.tenantAPI.openAIAppsChallengeToken` to the current draft's
public domain-verification value. The existing HTTPS 443 HTTPRoute exposes
`/.well-known/openai-apps-challenge` with the exact plain-text value, no login,
and no caching. Empty/invalid configuration returns 404. This is a public
ownership challenge, never an OAuth client secret or API credential. Verify
the URL before clicking Verify Domain in the OpenAI draft. The default remains
empty; this change alone does not verify a domain or publish a plugin.

### Registration completeness

Every public administrator operation is registered as an explicit MCP tool
with its own description, input schema and annotations. The internal backend
registry routes those reviewed tools but is not itself a public discovery or
dispatch interface. Missing tools remain unavailable; backend reachability is
not proof of publication, native API coverage or semantic workflow coverage.

### Collaboration credential isolation

Set `COLLABORATION_CREDENTIAL_NAMESPACE` to a dedicated Stalwart credential
namespace before enabling collaboration. Only collaboration connection operations
use this namespace; existing Harbor references continue using
`CREDENTIAL_NAMESPACE`. For existing deployments, an unset collaboration namespace
falls back to `CREDENTIAL_NAMESPACE`, then `credential-broker`.

The tenant-api service account needs only `get`, `create`, `update`, and `delete`
on Secrets in the dedicated namespace. Do not add collaboration write permissions
to the shared credential-broker namespace. Connection references generate Secret
names dynamically, so Kubernetes RBAC cannot enumerate resourceNames for create;
the dedicated namespace limits that authority. Tenant and subject ownership checks
remain mandatory in the application. Namespace changes do not migrate existing
credentials; revoke existing bindings before moving an enabled installation.

### Managed cluster Admin SSA acceptance

After promoting the signed core, call the current `byoc_connection_list` and use
its kubectl examples. For each reviewed context (`re8ch`, `re8ch-atlantic`),
preview then apply the exact merged-commit public fixture URL
`operations/releases/conformance-fixtures/cloud-managed-cluster-admin-v1.yaml`
with `--server-side --field-manager=re8ch-admin-binding-acceptance`. Read the
ConfigMap UID/data and the `default/kubernetes` Service UID through that context.
Preview/apply the sibling v2 fixture, verify the same per-target ConfigMap UID and
revision2, then preview/delete the temporary ConfigMap through the same context.
Both cluster API UIDs and object UIDs must differ. Preserve first failures;
unknown contexts reject before API execution. These temporary objects are
separate from the Actions executor's scoped delivery canary.

### Provider invocation tokens

The RFC 8693 compatibility exchange no longer returns the user's Supabase JWT.
It requires `INVOCATION_TOKEN_ISSUER_URL`, pinned `INVOCATION_TOKEN_ISSUER`, and
`INVOCATION_TOKEN_JWKS_URL`; missing or unavailable issuer/JWKS configuration
fails closed. JWKS verification accepts only a known `kid` with an RS256 key and
checks the issuer before the token can reach a provider. Signing keys remain in
the approved issuer and are never configured in tenant-mcp.

`RE8CH_TOOL_AUDIENCE` is a transitional generic audience for the compatibility
route. The final provider audience must be resolved by the binding authority from
the selected `binding_ref`; callers must never select or override a provider
audience. The verified active organization is likewise derived from the login
session and tenant-control decision, not request fields or headers.

Tenant-control revalidates the subject token with pg-identity and resolves the
ready binding through Bifurcate before signing. Its mounted
`tenant-invocation-signing/key-ring.json` supports one current and up to two
overlap keys; JWKS publishes only their public RSA parameters. The separate
`tenant-invocation-authority/BINDING_AUTHORITY_TOKEN` authenticates the bounded
PostgREST RPC. Git declares only empty Secret objects and the narrow rotator;
key and token values are generated in-cluster. The authority token is renewed
every 12 hours with a 36-hour expiry. The RSA key rotates after 23 days and its
public key remains in JWKS for a seven-day overlap; retiring private keys are
discarded. Until the rotator succeeds, issuance and JWKS fail closed while
unrelated tenant-control routes remain available.

The rotator CronJob and `supabase-billing-secrets` are both in
`supabase-billing`, so its `secretKeyRef` is namespace-local. The Job's service
account has no ClusterRole. A Role and RoleBinding in `tenant-infrastructure`
grant only `get/update/patch` on the two named output Secrets. The chart owns
only their metadata and intentionally omits `data`/`stringData`; Helm's
three-way upgrade therefore preserves the live-only data written by the
rotator. Do not enable force/replacement upgrades for these Secrets. A
retiring private key is deleted at rotation, while its public key stays in
JWKS for seven days—well beyond the five-minute maximum invocation-token life.

### Managed MCP compatibility projection

The 1.0.2 service tools reuse `service_catalog_list`,
`service_instance_list`, and `service_binding_list` to expose installed managed
MCP applications as `integration.mcp.managed.v1`. Managed applications do not
add platform or Kubernetes top-level tools.

Tenant Control may provide `GET /v1/tools/catalog`. Tenant MCP sends the
authenticated active organization only in `X-Tenant-Ref`, together with its
service credential; prompt arguments never select the organization. A response
contains `activeOrgRef` and Ready items with publication, revision,
installation, binding, service-instance, service-binding and tool references.
The authority must return only active-audience, installed, Ready and published
records. The client repeats organization and structural checks and fails closed
on mismatches. HTTP 404 or a response with no compatibility envelope preserves
the static catalog until the Billing list RPC lands.
