# tenant-api SQLite → existing Pigsty state migration proposal

Status: **Proposal — not migrated**, 2026-11-23. This document authorizes no database or runtime change.

Scope: tenant-api state persistence only. Existing recovery/publishing changes are independent; merge of this document is not migration or recovery evidence.

## Decision and incident evidence

- Reuse physical `pg-re8ch`; do not introduce another PostgreSQL cluster.
- Put tenant-api control state in existing `re8ch_identity`, dedicated `tenant_runtime` schema.
- Preserve existing GoTrue issuer, auth domain, OAuth clients, redirects and RP configuration.
- Incident evidence shows received usage plus its indexes account for nearly all SQLite state; control-state tables are comparatively small.
- Evidence reference: restricted incident bundle `tenant-api-disk-full-20261123` (dbstat, PVC capacity and CrashLoop inspection); raw telemetry remains outside Git.
- The immediate incident was full SQLite state PVC; the 4 GiB expansion is containment, not this migration.
- Keep SQLite authoritative until reviewed migration passes reconciliation and a fenced cutover.
- PostgreSQL does not automatically make the current RWO/singleton deployment highly available.
- Separately verify every writer, replica/leader ownership, local-state dependencies, node affinity and rescheduling behavior before relaxing singleton/RWO constraints.
- Reuse existing storage-event and GitOps operations; this proposal adds no custom planner/controller.

## Ownership and storage map

- `tenant_runtime.instances`, `operations`, `bindings`: service state, generation, idempotency and binding references.
- `cluster_registrations`, `cluster_connections`: BYOC identity/state, preserving existing refs and uniqueness.
- `mcp_catalog_revisions`, `mcp_catalog_heads`: immutable revisions and atomic current pointers.
- `dynamic_skills`: published/reviewed/revoked workflow state and reviewer identity.
- `credential_redemptions`, `credential_adoptions`: only token hashes, expiry and consumed state.
- `collaboration_connections`, `collaboration_writes`: user-owned connection state and write idempotency.
- `usage_outbox`: keep beside the business state that produces its event, in the same PG transaction.
- Initial `received_usage` destination: `tenant_runtime.received_usage`; decide partitioning/retention before import.
- Global deduplication lives in a separate unpartitioned event receipt table keyed by existing globally unique event_id.
- Atomically insert receipt (tenant/resource/canonical payload digest) and partitioned payload; partition-local uniqueness is insufficient.
- A replay of the same ID with different tenant/resource/payload fails, including after its original partition is archived.
- Keep receipts through the maximum producer retry, backup restore, billing replay and delayed delivery horizons; agree an explicit window before pruning.
- If receipts are ever expired, reject older events or consult retained archive receipts; do not silently accept them as new usage.
- Inventory actual SQLite files/tables/indexes and transaction call sites before implementation; do not assume one file.
- No credential values, raw tokens or provider secrets enter SQL migration reports or Git.

## Identity and catalog constraints

- Preserve opaque event/resource/operation/binding/catalog IDs byte-for-byte; do not regenerate UUIDs or refs.
- Preserve `tenant_ref` text exactly; FK to existing `core.organizations(tenant_ref)` in this same database.
- User-owned actor/subject UUIDs reference existing `auth.users(id)` after type/identity validation.
- Organization membership and active context remain canonical in `core`; never duplicate membership into runtime tables.
- Admin and Re8ch remain distinct organizations; personal tenant refs must not be collapsed into either.
- Validate all legacy tenant/subject refs first; quarantine/report orphans without inventing memberships or users.
- Service identities/review automation refs are not automatically auth.users UUIDs; keep explicit typed references.
- Keep tenant-scoped unique keys `(tenant_ref,name)` and `(tenant_ref,idempotency_key)`.
- Cross-table resource refs use tenant-inclusive keys/FKs to prevent cross-organization association.
- Catalog heads/revisions remain runtime publication data; link to authoritative identity catalog by its actual stable key.
- Verify current catalog relation/key names in schema inspection; do not invent a new catalog authority/table.
- Where catalog is exposed through an API rather than a relation, retain a validated stable ref, not a fake FK.
- Soft-delete organization state; avoid cascading organization deletion into audit, usage or billing evidence.

## Least privilege

- Dedicated NOLOGIN owner, separately controlled migrator, dedicated tenant-api LOGIN runtime role.
- Runtime role: CONNECT re8ch_identity, USAGE tenant_runtime, only required DML/sequences/functions there.
- No CREATE/DDL, superuser, BYPASSRLS, auth schema writes or reuse of GoTrue/service-role database credentials.
- Read only approved core/auth identity views or narrow functions; FK requirements reviewed by migrator.
- Tenant context derives from verified session; every query includes tenant key and membership enforcement.
- Add transaction-local tenant context/RLS defense with fail-closed missing context; test pooled connection reset.
- Admin cross-tenant operations retain explicit audited server authorization; no prompt-selected tenant privilege.
- Secrets provision through approved platform path; no DSN/password in Git or command logs.

## Atomicity and usage/Billing decision

- PG commit contains business mutation + operation/idempotency receipt + usage_outbox event together.
- External provider calls use durable intent/outbox and idempotency reconciliation, not a fictional distributed transaction.
- Outbox delivery is at-least-once; consumer deduplicates stable event_id and rejects same ID/different canonical payload.
- Mark delivered only after destination commit/ack; process crash before marker safely replays without charging twice.
- Preserve current event ID collision behavior and canonical payload comparison, including tenant/resource association.
- One-time exchange uses conditional UPDATE ... WHERE consumed_at IS NULL AND expires_at > now() RETURNING.
- Exactly one transaction wins under concurrent requests; consumption and any local binding/result record commit together.
- Preserve consumed hashes through rollback; do not resurrect an already redeemed token from an older snapshot.
- External credential delivery cannot be exactly-once with a DB transaction alone; persist handoff intent/result and define retry policy.
- Existing billing README confirms re8ch_billing owns Commerce/Payment, separate schemas/roles/secrets and subject UUID references.
- It explicitly prohibits cross-database FK, FDW, dblink, user mirror and profile cache; preserve that boundary.
- Therefore do not move raw usage directly into a monetary ledger or create cross-DB atomic dependencies now.
- Candidate later: approved metering inbox in re8ch_billing, consumed via identity-side outbox and idempotent event receipts.
- Billing owner must confirm whether raw metering, rated usage, invoices or only commerce entries belong there.
- Until confirmed, raw received_usage stays in identity runtime schema; no synchronous dual write across databases.
- Billing consumer stores canonical tenant/subject IDs and uses Identity API for mutable authorization, as existing contract requires.
- Define raw-event retention, rollups, archive/PITR and billing replay horizon before deleting any raw event.
- Time partitioning must preserve global event-ID deduplication: use a reviewed receipt registry/constraint design, not merely a partition-local unique key.
- Receipt retention must cover the billing replay horizon; expiring a partition must not make an old event chargeable again.

## Backfill and fenced cutover

1. Approved GitOps/SQL PRs define schema, grants, migration version and runtime backend flag; validate backup/PITR first.
2. Capture consistent SQLite backup including WAL via supported backup API; record hashes, versions, counts and high-water marks.
3. Restore to isolated migration target; import IDs, UTC instants, decimal quantities and JSON without lossy conversion.
4. Preserve canonical event bytes/hash separately where JSONB normalization could change idempotency comparisons.
5. Verify rows/tenant counts, content hashes, usage sums by tenant/resource/meter/period, outbox backlog and catalog heads.
6. Validate FK/orphans, uniqueness, expired/consumed token states and collaboration transitions before enabling PG writes.
7. For this bounded state size prefer a short scheduled write fence over unaudited CDC/dual-write complexity.
8. Stop/fence ALL writers: tenant-api replicas, usage receivers, controllers and background/outbox producers; drain inflight requests.
9. Final consistent snapshot + deterministic final delta/import; repeat reconciliation at one documented cutover watermark.
10. Start one PG writer generation; reject stale SQLite generation. Resume receivers only after PG read/write health passes.
11. Keep public contracts/responses compatible and compare read results; never send shadow writes to two authorities.
12. Retain encrypted immutable SQLite snapshots and migration receipts; keep old mount read-only during observation.

## Rollback and acceptance

- Before first PG write: switch back to fenced SQLite snapshot after confirming no PG mutations were accepted.
- After first PG write: prefer roll-forward; rollback requires fence PG, drain outbox, reverse-export ALL committed delta and reconcile.
- Never flip to stale SQLite or replay delivered events blindly; include consumption, revocation, operations and delivery receipts.
- Rollback is unavailable until reverse migration is rehearsed; document restore/PITR recovery and RPO/RTO before cutover approval.
- Test concurrent idempotency, same-ID payload conflict, tenant isolation and one-time token redemption race (one winner).
- Inject crashes before/after commit, before/after consumer ack and during catalog-head update; no lost event/double charge.
- Confirm unchanged OAuth code/refresh behavior, active Admin context and actual protected tool calls across all six edges.
- Verify service plans/previews, catalog publication, skills, BYOC and collaboration metadata using designated test resources.
- Fresh login and valid refresh are separate acceptance items; existing session success alone is insufficient.
- Observe pool exhaustion/timeouts, DB/table growth, partitions, outbox age, API error rate and PG availability under load.
- Disk-pressure rehearsal must fail writes visibly without accepting usage then dropping it or making all tenant APIs unavailable.
- PG migration alone does not provide API HA: remove RWO and current admin-api co-location constraints only through a separately verified deployment change.
- After concurrency and idempotency checks pass, evaluate multiple stateless API replicas and rolling updates; do not assume the present Recreate singleton is already HA.
- Success evidence includes runtime calls + audit refs + reconciled data, not merely a merged PR or deployed image.

## Source context and review gates

- [Runtime ledger and usage transactions](../../platform/apps/tenant-mcp/ledger.go), [redemption](../../platform/apps/tenant-mcp/credentials.go), [skills](../../platform/apps/tenant-mcp/skills.go), [catalog](../../platform/apps/tenant-mcp/mcp_catalog.go), [collaboration](../../platform/apps/tenant-mcp/collaboration_bindings.go).
- [Existing Identity organization context](../supabase-identity/tenant-runtime-contexts.yaml) and [Billing isolation contract](../supabase-billing/README.md).
- Review must confirm actual catalog relation/key, inventory all writers and SQLite files, prove backup/PITR and deduplication retention, and obtain Identity/Billing ownership approval before implementation.
- Implementation, schema/grants, data movement and cutover require separate reviewed changes and runtime acceptance; none are included here.
