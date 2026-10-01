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
