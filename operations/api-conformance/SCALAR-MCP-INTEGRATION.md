# Scalar MCP integration for large API catalogs

Status: design and client-configuration template; no Scalar installation is
created or activated by this declaration.

## Decision

Use Git-reviewed OpenAPI documents and operation allowlists as the authority for
the RE8CH API catalog. Scalar may index those documents and expose selected
operations through an installation-specific remote MCP endpoint. ToolHive
remains the RE8CH-facing aggregation, authentication, authorization and audit
boundary when the Scalar endpoint is published to shared agents.

Scalar does not replace the API conformance controller, typed domain adapters,
tenant checks, Kubernetes RBAC, or the target service's idempotency and workflow
logic. Registration or discovery never authorizes execution.

## Endpoint distinction

Do not configure the bare `https://mcp.scalar.com/` origin as an API server.
Scalar exposes two different MCP surfaces:

- `https://scalar.com/mcp` searches Scalar's public product documentation. It
  does not call RE8CH APIs.
- `https://mcp.scalar.com/mcp/<INSTALL_ID>` is an installation-specific API MCP
  endpoint generated from one or more OpenAPI documents.

The checked-in client template is
[`scalar-mcp.example.toml`](scalar-mcp.example.toml). The installation ID is
non-secret metadata but must identify a reviewed installation. Credentials and
authorization headers must be supplied by the approved credential system or an
interactive OAuth flow, never by Git.

## Large-catalog pattern

Hundreds of operations must not become hundreds of always-loaded client tools.
Use Scalar's search-and-execute model so the client sees a small, stable tool
surface and retrieves operation schemas on demand.

Keep the following desired metadata in Git for every published API:

| Field | Requirement |
| --- | --- |
| API identity | Stable service and version identifier |
| OpenAPI source | Reviewed HTTPS URL or repository path |
| Schema digest | Pinned SHA-256 of the bundled document |
| Operations | Explicit search and execute operation ID allowlists |
| Authority | Owning repository, team and runtime authority |
| Environments | Non-secret names and base URLs only |
| Authentication | Mode, scopes and secret reference names only |
| Mutation policy | Plan, approval, idempotency and cleanup requirements |
| Tenant boundary | Authenticated tenant and active-membership checks |
| Acceptance | Contract, authorization and semantic workflow evidence |
| Scalar binding | MCP server/installation identifiers after creation |

OpenAPI remains the common source for API reference, request schemas and MCP
operation metadata. Multi-file specifications must be bundled before upload so
that schema resolution is deterministic.

## Publication flow

1. Add or update the OpenAPI source and its digest in a reviewed declaration.
2. Run linting, schema validation and bounded conformance checks.
3. Classify every operation as omitted, search-only or executable. New
   mutations default to omitted or search-only.
4. Apply the reviewed configuration to Scalar using its supported API or
   dashboard. Never place a PAT or upstream credential in a command argument,
   workflow log or repository file.
5. Read back the effective Scalar server, environment, installation and access
   group state; record only non-secret identifiers and the verification result.
6. Register the installation-specific remote MCP with ToolHive when shared
   RE8CH access is required. ToolHive policy must restrict the visible tools and
   caller groups; the target API remains the final authorization boundary.
7. Verify `tools/list`, one representative read operation, authorization denial,
   and each enabled mutation's semantic postcondition.

Git stores desired configuration and evidence. Scalar enforces its hosted MCP
configuration, ToolHive enforces the RE8CH gateway policy, and the target API
enforces business authorization. A committed declaration alone is not proof
that any of those three effective states match.

## Authentication rules

- Prefer per-user OAuth or passthrough authentication where target APIs must
  preserve the caller's identity and tenant.
- A shared Scalar installation credential is acceptable only for a narrowly
  scoped service identity whose downstream permissions and audit identity are
  explicit.
- Store only secret names, purpose, scope and rotation expectations in Git.
- Do not commit Scalar PATs, access tokens, refresh tokens, API keys or resolved
  secret values.
- Do not let a Scalar or ToolHive success bypass the target service's tenant,
  membership, resource-ownership or mutation checks.

## Relationship to current RE8CH controls

Existing `APIConformanceTarget` declarations already pin schema digests,
allowed hosts, operation IDs, budgets, canary tenants and cleanup contracts.
Reuse those declarations instead of creating a second API inventory. Scalar
publication metadata should reference them or be generated from the same
reviewed source.

For complex operations, keep a typed domain tool such as `plan_*`, `apply_*`
and `get_operation_status` in the RE8CH backend. Scalar is appropriate for
well-described HTTP operations; it is not the workflow engine for long-running
transactions, compensating actions or cluster reconciliation.

## Activation blockers

This repository does not currently contain a Scalar installation ID, reviewed
Scalar organization binding, credential reference, or verified effective-state
readback. Until those exist, the example URL must remain a placeholder and no
Scalar endpoint should be advertised as a production RE8CH API surface.

## References

- Scalar hosted MCP servers: <https://scalar.com/products/agent/mcp>
- Scalar MCP configuration: <https://scalar.com/products/agent/configuration>
- Scalar API MCP management operations: <https://scalar.com/tools/api/tag/mcp>
