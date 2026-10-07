# Supabase identity and shared runtime tenants

## Current request path

`/tenant/mcp` is a ConnecTool VirtualMCPServer publication. ToolHive checks a
Supabase GoTrue token with issuer `https://db-rw.re8ch.com/auth/v1` and audience
`authenticated`; its tenant publication policy permits the authenticated
principal. Token exchange forwards that verified user token to `tenant-api`.
The Go backend verifies the session again, then asks tenant-control to resolve
the user's active Supabase organization membership. The resolver currently
requires a Tenant CR whose name equals that organization reference. Supabase
automatically creates a Personal organization at signup, but does not create a
Tenant CR. Those are separate authorities.

Authentication proves the subject. Supabase membership authorizes an
organization. A Tenant CR describes a runtime resource pool. None of these
identifiers should be substituted for another.

## Shared class contract

For ordinary Personal accounts, use one reviewed runtime Tenant resource for
the Personal user class, rather than one Tenant CR per account. Keep the
Supabase organization and user as separate verified fields:

| Field | Authority | Meaning |
| --- | --- | --- |
| `subjectRef` | verified GoTrue session | Human user UUID; never supplied in a tool argument |
| `organizationRef` | active Supabase membership | Logical owner and authorization scope, such as `personal-<UUID>` |
| `runtimeTenantRef` | reviewed class mapping | Shared cluster policy, capacity and aggregate quota |
| `role` | active Supabase membership | Allowed actions inside the organization |

The mapping must select a class from the verified organization kind, not an
email address, browser claim or arbitrary tenant header. A requested
organization is accepted only when Supabase confirms current membership.
The protected `admin` organization cannot map into the Personal class.
Revoked membership must stop subsequent operations without waiting for a
Tenant CR change.

`runtimeTenantRef` is not a data ownership key. A shared class must never be
passed into an existing query that currently uses `tenantRef` as its sole
filter. Service instances, bindings, operations, usage, BYOC registrations,
dynamic skills and resource references must retain organization or subject
scope. Server-generated owner fields must be immutable and included in every
list, get, update, delete and idempotency lookup. Aggregate class quota and
per-owner quota are different decisions.

## Rollout gates

1. Keep login and MCP initialization independent of runtime authorization.
   The `tenant_access_status` tool is the only tool for a verified subject
   whose runtime context cannot be resolved. An invalid token gets 401; an
   unavailable authorizer gets 503.
2. The Supabase context RPC returns active membership and organization kind.
   The resolver returns both `tenantRef` (logical owner) and
   `runtimeTenantRef` (runtime class), and a single `personal-standard` Tenant
   describes the Personal class. Existing Personal organizations with their
   own active Tenant CR keep that runtime. The Go backend deliberately leaves
   its legacy `Tenant` field empty for a shared class, exposing only
   `tenant_access_status`. It must never rewrite that field to the class name:
   its SQLite stores currently filter primarily by `tenant_ref`.
3. Before enabling each tool family for the shared class, enforce ownership
   for its backing objects. TenantResourceClaim currently has no owner field;
   tenant-control claim lists and mutations filter by runtime tenant only.
   Generic Kubernetes operations also need a per-owner namespace or an
   equivalent enforced owner boundary. Harbor and Grafana native permissions
   need their own per-owner binding checks.
4. Verify two newly registered users map to the same runtime class but cannot
   list, read, mutate, revoke or replay each other's resources or usage. Verify
   membership revocation, admin isolation, authorizer outage, and aggregate
   quota separately.

The shared class is not enabled by the MCP login boundary change. Until the
ownership gates pass, connecting successfully must not grant shared access to
cluster objects or tenant data.
