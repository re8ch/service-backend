# Cloud execution and local Flux retirement

Operator-approved target (2026-10-27): build, publish and reconcile in the cloud,
reuse the existing SSA executor and accepted immutable artifacts, and retire local
Flux controllers after their resources have another verified owner. Do not move
SSA releases back to Flux or introduce a cloud Flux cluster. A Flux-like CLI is a
later adapter over these operations, not a requirement to implement Flux APIs.

## First executable slice

The existing `main-domain-release.yml` retains its OIDC workflow identity,
protected environments, exact domain RBAC and per-contract concurrency. Its jobs
run on GitHub-hosted Ubuntu. `mode=publish` retains compilation/signing for new
release inputs. `mode=reconcile` skips compilation/signing and resolves the
immutable artifact from the release's durable accepted state:

```sh
gh workflow run main-domain-release.yml --ref main \
  -f contract=contracts/releases/main-domain/main-data-storage-toolhive-system.yaml \
  -f mode=reconcile -f execute=true
```

Reconcile verifies target identity, accepted health, repository, inventory and
signature before invoking the existing dry-run/apply/health/prune executor. A
changed contract must pass the executor's original bundle-contract binding; it
cannot silently repurpose an older bundle. Suspended or blocked releases fail
closed. No caller-selected artifact URL or unrestricted force flag is exposed.
Publish and reconcile share the same concurrency key and canonical contract path.

Cloud canary run `40503894800` passed OIDC authentication, exact RBAC denial
checks, and signature verification of the existing cloud-disk artifact. It stopped
before applying resources because that accepted v1alpha1 artifact predates stage
metadata. Missing stages retain the original single-runtime-stage semantics;
new compilers continue emitting explicit stages.

PR #2292 also changed cloud-disk's contract binding when it split the executor
environment/audience by domain. `accepted-contract-migrations.json` explicitly
authorizes this old/new contract pair and the one already accepted immutable
artifact. Tests reconstruct the old contract by reverting only that domain
classification and provenance change. Runtime target, resource permissions,
inventory, field manager, lifecycle and signer are unchanged. The exception is
limited to reconcile of that healthy accepted digest and never permits a new
artifact, changed contract, force operation or first deployment. Current domain
OIDC/RBAC, payload digest, signature and inventory checks still apply.

The follow-up cloud run `40504842574` completed successfully on GitHub-hosted
workers with compilation/signing skipped. Runtime readback confirmed a healthy
result, sequence advanced from 1 to 2 and the accepted cloud-disk digest unchanged.

The remaining main-repository cohort was checked against its accepted state and
Git history. Nine releases have unchanged bundle source since PR #2223 and only
the PR #2292 domain provenance change; their exact binding transitions are now
listed together. `main-commerce-billing-payments` is excluded: PR #2370 changed
its inventory and routes, so it requires a new artifact for that changed
configuration rather than replay of the older accepted inventory. This does not
authorize overwriting newer desired source with an older accepted snapshot.

This is an explicit dispatch interface; it does not yet claim automatic
merge-to-release, periodic drift repair, or complete local Flux retirement.
The durable acceptance ConfigMap remains an existing runtime record, not a local
controller. Cloud executors need authenticated connectivity to the Kubernetes API
and registry. Failure to reach either must remain a failed operation.

## Reuse and remaining cutover

PR #2231 accepted 19 SSA releases. Later PRs #2353 and #2358 returned Supabase REST
and Headlamp to Flux. The current main cohort has 14 releases; the three Ceph
StaticReleases and their accepted state are separate existing work to preserve.
Do not infer missing implementation from stale inventory/shadow milestones.

1. Prove cloud replay of an existing accepted artifact and retain runtime health
   evidence. Do not rebuild merely to make this proof pass.
2. Separate build/publish from promotion and reuse the reviewed immutable digest
   on subsequent deployment and reconcile. Add event/manual triggers through
   existing GitHub mechanisms; periodic drift checks need an explicit freshness
   expectation because Actions schedules can be delayed.
3. Extend the same operation boundary to autonomous-domain and Ceph releases,
   keeping their repository/environment identities. Move their execution off R640
   as connectivity and protected workflow acceptance are verified.
4. Inventory residual Helm hooks/lifecycle, encrypted configuration, CRDs/RBAC and
   native-owned resources. Transfer their ownership without deleting workloads,
   overwriting operator/HPA-owned fields, or duplicating writers. Existing native
   owners remain temporary until each replacement passes acceptance.
5. Stop and remove local Flux controllers only after all actual consumers and
   source dependencies are covered. Preserve data, release state and rollback
   evidence. The rollback path must not silently resume a second writer.

The external adapter will expose release status, reconcile, suspend, resume and
approved rollback. Reconcile means replaying the approved digest with fresh
runtime verification, not recompiling source or fabricating Ready conditions.

## Implementation boundaries

No replacement planner or permanent service is introduced in the first slice.
The native gap is that the existing workflow always compiled before execution;
the small state selector and workflow branch enable artifact-only replay.

Keep public runtime inventory withdrawn as required by PR #2402. No secrets,
private topology or raw runtime state belongs in public Actions evidence.
Start from the rewritten signed main established by PR #2419; never merge its
pre-redaction ancestry back into the repository.
