# Self-service plugin operation completeness

Status: implementation in progress; not a completion report.

## Required outcome

Register all cluster plugin operations in RE8CH self-service and the management
tools surface, including advanced-fabric (advfab), public-edge, mihomos-cluster
(user spelling: mihomos-xluster), Rook/Ceph, and anatomy. Maintain an OpenAPI
capability table with operation-level schema conformance and semantic workflow
coverage. Generic Kubernetes access is not evidence that a plugin's native
operations and UI workflows are complete.

## Evidence inspected on 2026-10-04

- Workspace doctor: healthy, 58 catalog modules.
- GitHub base: `30c3b46ee`; dedicated branch `feat/self-service-plugin-capabilities`.
- Authenticated `platform_tool_catalog`: cloud, infra, admin, native and
  tenant_admin backends available. No backend reported unavailable.
- Live `get_platform_service_catalog`: 95 namespaces, 365 workloads, 356
  Services; 50 namespaces have unclassified configuration authority. This is
  one platform inventory, not proof of coverage of every connected cluster.
- Registry code: `platform/apps/tenant-mcp/module_registry.go`; explicit
  allowlists in `platform-modules.json`; admin dispatch in `platform_tools.go`.
- User-confirmed surface: RE8CH management tools directory, implemented by
  ToolHive Cloud UI at `https://tools.re8ch.com`. Its registry metadata differs
  from the Headlamp tenant tool catalog.

## Capability audit table

| Domain | Observed published operations | Missing proof / next implementation |
| --- | --- | --- |
| advfab | No dedicated operations in current catalog | Enumerate authoritative API and CRD operations and Headlamp workflows; implement adapters and registration |
| public edge | No dedicated operations in current catalog | Enumerate manager API, lifecycle and UI workflows; preserve public authority inventory |
| mihomos-cluster | No dedicated operations in current catalog | Enumerate cluster API and workflow contracts, per-cluster targeting and authorization |
| Rook/Ceph | get_ceph_storage_status, get_ceph_recovery_status, restart_ceph_osd_1 | Inventory remaining native operations; generic health/restart does not establish completeness |
| anatomy | get_runtime_storage_analyses | Verify exact anatomy product/CRDs, creation/lifecycle/results and UI workflows |
| All other cluster plugins | Generic live service inventory exists | Reconcile PluginDefinitions, installed plugins, connected clusters and native APIs; explicitly report unclassified plugins |

## Required evidence for each operation

Track cluster, plugin/version, native authority, HTTP method/path/operationId
(or CRD equivalent), request/response schema references, MCP backend/tool name,
registration state, permission/role, mutation policy, plan/idempotency behavior,
audit reference, semantic precondition/action/postcondition, and cleanup.
Record unsupported or unavailable operations explicitly; do not invent HTTP
endpoints for MCP-only operations.

Schemathesis verification must execute against actual OpenAPI operations and
record schema version, cases, response validation and authorization boundaries.
Stagehand verification must exercise the actual management UI and record
semantic postconditions, including failure behavior and state verification.
A static matrix, passing unit tests, or tools/list alone is insufficient.

## Delivery gates still outstanding

1. Complete authoritative plugin and connected-cluster inventory.
2. Extract complete native operation schemas and semantic workflows.
3. Implement typed adapters and management registration for all operations.
4. Publish the OpenAPI capability matrix linked to those adapters.
5. Run contract, authorization, idempotency and semantic browser acceptance.
6. Signed commits, PR review, successful Actions and approved GitOps rollout.
7. Re-query live catalog and verify each operation and UI flow after rollout.

The first registry change exposes missing allowlisted publications as
`missingTools`, separately from unavailable backends. It supplies gap evidence
for subsequent registration work and does not satisfy the full delivery gates.

## Current implementation evidence

The target is the RE8CH management tools directory (user-confirmed), not only
Headlamp or Codex. See [the native OpenAPI inventory](../plugin-capabilities/README.md)
for eight captured Kubernetes contracts (578 operations) and 13 source-derived
Mihomo controller read contracts, with reproducible Schemathesis 4.29.1 offline
enumeration. Four discovery/read entry points, 197 individual Kubernetes GET
tools and 15 Mihomos tools are implemented. The directory contains 212 tool
definitions and 618 native operation inventory rows. They remain pending release
and authenticated invocation. All 381 Kubernetes mutations, 27 other inventoried
Mihomo routes and conditional/non-Kubernetes surfaces remain incomplete.

The pinned UI patch exposes operation contracts, parameters, registration,
authority and independent HTTP/semantic acceptance states. Stagehand 4.1.0 has
an executable inventory-navigation runner; its four offline tests do not
establish browser or operation semantic acceptance. Configuration is still
required for a live run.

Validation at the current implementation: 105 focused backend/release tests,
213 upstream UI tests with both owned patches, TypeScript, deterministic
regeneration and ConfigMap rendering passed. Two pre-existing UI lint warnings
remain. Sampled JSON response bodies and six live schema parity checks have
separate evidence reports; none establishes deployed adapter invocation or full
HTTP conformance. Signed release and an independent GitOps promotion remain
required before live tool and UI acceptance.
