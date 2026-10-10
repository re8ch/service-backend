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

Tenant owners and operators can manage allowlisted ConfigMaps, workloads and
ClusterIP Services through the native Kubernetes tools. Secret values use the
separate `tenant_secret_*` broker: values are write-only, while reads expose
only names, key names and Kubernetes metadata. Public HTTP access uses
`tenant_exposure_*`, which creates a constrained HTTPRoute to a same-namespace
ClusterIP Service. Hostnames are allocated below `apps.re8ch.com`; the shared
Gateway, listener, DNS and TLS policy remain platform-owned. Raw Secret and
Gateway access are not published.

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

The tenant MCP also exposes `platform_tool_catalog` and `platform_tool_call`
only to a verified user with a current `owner` or `admin` membership in the
hidden `admin` organization while `admin` is the account's active organization.
It reads the Supabase membership projection once per verified login session and
caches that decision in the tenant process.
A new login session receives a fresh decision; a process restart clears the
cache. The service bearer cannot use it. The call
tool uses a fixed allowlist of published Cloud/Infra/Admin and native Cloud
Platform tool names; backend discovery cannot silently grant a new tool.
Backend credentials are read by the tenant runtime's Kubernetes
service account from three named Secrets via tightly scoped `get` RBAC and are
never returned to the caller. The old Admin and Cloud/Infra MCP connections
remain published.

The authenticated self-service plugin is the preferred administrator entry.
An active `admin` organization session with verified platform-admin membership
uses the platform catalog's separate allowlist and backend permissions. Empty
tenant consumables or service classes do not restrict this administrator path.
The `admin` backend publishes `postgres_admin_sql` for reviewed Pigsty SQL:
read-only queries execute through the fixed Supabase database identity; writes
require an approval ID, first return a plan hash, and execute only with
`apply=true` and that hash.
Neither callers nor tool results receive the database password. This path
avoids requiring a fresh Supabase Studio login for every migration.

Run `go test -race ./...` and `go vet ./...` in this directory. The repository
chart tests are `python3 -m pytest tests/test_tenant_infrastructure_gitops.py`.
Run `python3 differential_test.py` here to start both tenant runtimes against
the same fake identity services and copied SQLite fixture. It compares all
legacy tool schemas, selected read and write results, tenant resources, OAuth,
billing, authorization boundaries, and idempotent replay. Resource list order
is normalized because the Go SDK sorts the list by URI.
Build locally from the repository root with
`docker build -f platform/apps/tenant-mcp/Dockerfile -t re8ch-tenant-mcp:local .`.

The `tenant-go-release` GitHub workflow validates PRs. On `main`, its trusted
Macmini job publishes an amd64 Harbor image, signs the immutable digest, and
uploads `tenant-mcp.image` plus an SPDX SBOM. After that release succeeds,
create a separate signed GitOps PR setting both
`tenantPlatform.tenantAPI.enabled: true` and
`tenantPlatform.tenantAPI.image: <verified image digest>` in
`platform/birfucate-metering/release.yaml`. Verify image signature, deployment
readiness, tenant ToolHive canary, tenant billing, and credential redemption
before considering the migration complete. Rollback is the previous HelmRelease
revision with `tenantAPI.enabled: false`; SQLite tenant state remains compatible.

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
Only GitOps administrators may change module URLs, credential references, and
explicit tool allowlists. Credentials never appear in the registry.

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

`platform_tool_catalog` returns `missingTools` alongside callable `tools` and
`unavailableBackends`. Each missing entry identifies an allowlisted operation
that a successfully discovered backend did not publish. Unavailable backends
remain unknown, not proof of missing operations. These fields measure registry
publication only; they do not prove native API or semantic workflow coverage.
