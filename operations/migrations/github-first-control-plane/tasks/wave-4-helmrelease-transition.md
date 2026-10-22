# Task: classify and safely transition HelmRelease ownership

## Outcome

Classify every active `HelmRelease` and move only qualified releases from
`helm-controller` to the shared immutable-bundle execution path. This task does
not build a second Helm-specific executor. It produces the release inventory,
locks, rendered bundles and ownership hand-off records consumed by the executor
defined in [the companion task](wave-5-unified-release-executor.md).

Wave 4 and Wave 5 are intended to run together. Classification and shadow
compilation can start immediately; a release may not cut over until the shared
executor passes its canary gates.

## Scope

- Inventory every live and declared `HelmRelease`, including suspended objects.
- Record chart, source, values, images, dependencies, hooks, tests, remediation,
  release storage, target namespace and current field ownership.
- Assign one explicit disposition to each release:
  - `retain-helm-lifecycle`
  - `bundle-ssa-candidate`
  - `blocked-pending-evidence`
- Produce a `ReleaseContract` and immutable OCI bundle for each qualified
  `bundle-ssa-candidate`.
- Prove a safe, reversible ownership transfer without triggering Helm uninstall
  or Flux prune.
- Remove a `HelmRelease` only after the replacement owns and observes the exact
  approved bundle.

Out of scope:

- Bulk conversion based only on successful `helm template` output.
- Networking, cluster authentication, storage control planes or other
  high-blast-radius releases as the first canary.
- Plaintext Secret values in GitHub, OCI metadata, logs, Issues or evidence.
- Arbitrary `kubectl`, Helm CLI or shell input accepted from a workflow.

## Inputs and authorities

- Git `main` is the desired-state authority.
- Kubernetes and Helm release records are runtime evidence, not a source to copy
  back into Git automatically.
- [Cloud desired-state authority](../cloud-desired-state-authority.md) defines
  `ReleaseContract` and Secret reference boundaries.
- [Flux controller capability catalog](../../../flux-lightweight/controller-capabilities.yaml)
  defines the Helm lifecycle responsibilities that must either be retained or
  replaced.
- The shared executor contract, operation allowlist and audit requirements come
  from [Wave 5](wave-5-unified-release-executor.md).

## Required inventory record

Create one reviewable record per release with at least:

| Field | Purpose |
| --- | --- |
| Stable release ID and owning domain | Prevent rename from changing identity |
| Chart/source version and digest | Eliminate floating execution inputs |
| Values inputs and secret references | Reproduce rendering without exposing values |
| Rendered object identities | Establish executor scope and inventory |
| Images and digests | Prove the workload actually promoted |
| Hooks and ordering | Detect behavior lost by static rendering |
| Tests | Decide whether Helm lifecycle remains necessary |
| Install/upgrade remediation | Preserve rollback and retry semantics |
| Helm storage/history dependency | Determine whether history is operationally required |
| CRD behavior | Prevent unsafe CRD replacement or deletion |
| `dependsOn` and health behavior | Preserve rollout ordering and readiness |
| Flux/Helm finalizers and field managers | Design the ownership transfer |
| Rollback owner and previous digest | Make reversal executable |
| Disposition and evidence | Make the decision auditable |

The inventory may contain resource names but must not publish Secret values,
credentials, private endpoints, node identities or other restricted topology.
Sensitive inventory is stored as an encrypted repository object or restricted
workflow artifact according to its data classification.

## Classification decision

Choose `retain-helm-lifecycle` when any required behavior has no proven executor
equivalent, including:

- pre/post install, upgrade, rollback or delete hooks;
- Helm tests required for promotion;
- operational dependence on Helm release history;
- install/upgrade remediation or rollback behavior not reproduced and tested;
- chart-managed CRD lifecycle that cannot be safely expressed as ordered SSA;
- dynamic lookup or other rendering that depends on live cluster state;
- release-specific deletion semantics that cannot be orphaned safely.

Choose `bundle-ssa-candidate` only when:

- rendering is deterministic from locked inputs;
- the rendered object set is complete and schema/policy clean;
- all images and sources are immutable;
- lifecycle behavior is expressible through the shared executor;
- prune and rollback inventories are explicit;
- no other controller writes the same fields after cutover.

Anything uncertain is `blocked-pending-evidence`; uncertainty must never default
to conversion.

## Work plan

### 1. Capture the baseline

1. Compile the repository declaration inventory.
2. Read live release status, Helm storage and managed fields through a read-only
   identity.
3. Normalize and hash the rendered resources without storing Secret plaintext.
4. Record current readiness, revision, failure/remediation state and finalizers.
5. Reconcile declared releases with live releases and classify discrepancies.

Deliverable: a complete inventory with one disposition per release and no
unowned entry.

### 2. Build shadow bundles

For each candidate, GitHub-hosted Actions must:

1. Fetch chart and values only from locked sources.
2. Render with a pinned Helm version and declared API capability set.
3. Validate schemas, policy, namespace and object allowlists.
4. Extract the exact inventory and image digests.
5. Publish a signed OCI bundle and provenance statement.
6. Compare normalized output with the controller-rendered runtime release.

The shadow job has no Kubernetes write permission. A difference becomes a Check
and a deduplicated maintenance item; it is not automatically accepted.

### 3. Qualify a canary

Select a low-risk, namespace-scoped release without hooks, tests, CRDs, public
traffic, storage authority, authentication authority or cluster-wide RBAC.
Require repeated deterministic builds and a restore drill before cutover.

### 4. Transfer ownership safely

The exact sequence is release-specific and must be rehearsed on the canary:

1. Freeze source revision and record the last good Helm revision and bundle
   digest.
2. Prevent new Helm reconciliation without deleting the release.
3. Render and verify that the replacement inventory matches the intended live
   inventory.
4. Apply the approved bundle with a dedicated SSA field manager and a bounded
   object allowlist.
5. Detect conflicts; do not use force ownership as a blanket migration tool.
6. Verify health, observed image digests, audit correlation and rollback.
7. Remove or neutralize Helm/Flux deletion finalizers using a reviewed,
   release-specific orphan procedure before deleting the `HelmRelease`.
8. Confirm deletion does not uninstall or mutate replacement-owned resources.
9. Remove obsolete Helm source objects only after proving that no remaining
   release consumes them.

No step may leave Helm and the executor as concurrent writers. Merely suspending
a `HelmRelease` does not prove safe finalizer behavior.

### 5. Expand by risk cohort

Migrate namespace-scoped stateless releases first, then ordinary stateful
releases. Shared infrastructure, networking, identity, secrets and storage move
only after their specific failure and rollback drills pass. Releases retaining
Helm lifecycle remain managed by a reduced `helm-controller` installation.

## Tests and evidence

- Two builds from identical inputs produce byte-identical bundles.
- Floating Git, chart and image inputs fail admission.
- Secret values cannot appear in bundles, provenance or logs.
- Shadow inventory matches the approved Helm inventory.
- SSA dry-run detects ownership conflicts before execution.
- Suspend, orphan, deletion and rollback are tested independently.
- Deleting the former `HelmRelease` does not uninstall the canary.
- A failed health check restores the previous approved digest.
- A GitHub outage leaves the last accepted release running and does not prune.
- Observer evidence identifies the applied digest and operation ID.

## Rollback

Before cutover, rollback means resume the unchanged `HelmRelease`.

After executor ownership is established:

1. stop further executor operations for the release;
2. apply the previous approved bundle digest;
3. verify health and inventory;
4. restore Helm ownership only from the recorded chart, values and release
   history, with an explicit field-ownership plan;
5. never recreate a HelmRelease while the executor still writes the same fields.

## Completion criteria

- Every release has a reviewed disposition and rollback owner.
- Every converted release is backed by a signed immutable bundle and exact
  inventory.
- No release has two reconcilers or ambiguous field ownership.
- All retained Helm lifecycle requirements are documented.
- Source and controller capacity are reduced only in proportion to migrated
  releases.
- `helm-controller` is removed only when zero retained releases and zero Helm
  finalizers remain.

