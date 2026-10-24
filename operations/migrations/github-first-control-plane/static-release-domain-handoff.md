# Static release executor handoff and domain migration playbook

Status: Ceph production path proven on 2026-10-24; domain migrations ready to
dispatch independently.

This document is the handoff for replacing Flux Helm/Kustomize execution with
GitHub-compiled, signed OCI bundles and bounded Kubernetes Server-Side Apply
(SSA). It records what is already operational, the evidence behind it, the
mistakes found during the cutover, and the exact gates every autonomous-domain
task must satisfy.

Use the machine-readable
[`static-release-domain-dispatch.yaml`](static-release-domain-dispatch.yaml) to
create the four autonomous-domain tasks and the five main-domain split tasks.
The shared contract remains
[`tasks/wave-5-unified-release-executor.md`](tasks/wave-5-unified-release-executor.md);
this document describes the proven implementation and how to repeat it.

## Outcome already achieved

The production path now is:

```text
Git ReleaseContract + locked renderer inputs
        |
        v
GitHub deterministic Helm/Kustomize compiler
        |
        v
keyless-signed OCI bundle in GHCR
        |
        v
OIDC-protected copy + signature verification in Harbor
        |
        v
Git-projected StaticRelease + immutable ReleaseOperation
        |
        v
re8ch-release-dispatcher GitHub App
        |
        v
protected Actions environment + audience-specific identity
        |
        v
bounded SSA stages + durable inventory + health acceptance
        |
        v
status, events, audit evidence and Flux-compatible projection
```

Git and the signed OCI digest are desired-state authority. Kubernetes remains
runtime authority. Flux CR status and metrics are compatibility projections;
they cannot become a second desired-state input.

The first production cohort consists of:

| StaticRelease | Locked input | Rendered objects | Applied bundle digest |
| --- | --- | ---: | --- |
| `rook-ceph` | `rook-ceph` `v1.20.3` | 83 | `sha256:3c7f536d6f9e5dc799a97998c7e4643b65853f796917748753338d4e990f1856` |
| `ceph-csi-drivers` | `ceph-csi-drivers` `1.0.4` | 23 | `sha256:be4732be7a612517deb37adea3dd733701968b2494de2baeb2ffcb7b61bbd716` |
| `rook-ceph-cluster` | `rook-ceph-cluster` `v1.20.3` | 7 | `sha256:032726de18cd2b7f0511f9631fd1fcbbfa032627f1144405c319e6f203c78b3f` |

All three have been taken over by SSA. The final compatibility reconcile proof
created `ReleaseOperation/rook-ceph-9cd3f4260b72b501`, dispatched GitHub Actions
run `40026749978`, completed the infra/runtime/accept jobs, and terminated as
`Succeeded/healthy`. The `rook-ceph` accepted and applied digests matched, its
`Healthy` condition was `True/HealthAcceptancePassed`, the Rook operator was
available, and the CephCluster phase was `Ready`.

The compatibility controller and dispatcher run from the signed Harbor image:

```text
registry.re8ch.com/functions-shared/release-compat-controller@sha256:c691ff19e5d368008556c4274c749ab2f3510843551b8b7a3b31becc7954eda9
```

The proven source revision is
`c78ed25c31260fbb4404b54b719cd824481dcd89`.

## Components and authority boundaries

### GitHub identities

`re8ch-release-dispatcher` is a private GitHub App installed only on
`re8ch/service-backend`.

| Property | Value |
| --- | --- |
| App ID | `5447147` |
| Installation ID | `174617158` |
| Repository permission | Actions: write |
| Implicit permission | Metadata: read |
| Contents permission | none |
| Webhook | disabled |
| Subscribed events | none |

Its cluster credential is
`Secret/flux-system/re8ch-release-dispatcher-github-app`. Never copy the private
key into GitHub Actions, logs, issues, task descriptions, or Git. The downloaded
bootstrap PEM was verified, imported, and deleted from the workstation.

The dispatcher receives no Kubernetes workload permissions. It reads immutable
`ReleaseOperation` objects, rejects expired operations, mints a short-lived App
installation token, and calls only the protected workflow dispatch endpoint.

The image promoter is a separate GitHub App. It opens a signed activation PR
that replaces the controller image by Harbor digest. Build workflows cannot
write `main` directly.

### Kubernetes identities

Ceph proves the required split:

- `release-executor-ceph-infra` handles declared CRD, cluster RBAC and protected
  storage-class stages.
- `release-executor-ceph-runtime` handles namespaced operator/runtime and Ceph
  custom-resource stages.

Both are short-lived GitHub Actions OIDC identities with independent audiences,
environments and allowlists. Neither can read Secrets, exec into Pods, issue
service-account tokens, impersonate users or perform unrestricted deletes.

Every new domain must receive its own audience, protected environment, RBAC,
inventory admission policy and SSA field-manager identity. Reusing the Ceph
identity or creating a cross-domain executor group is a migration failure.

### Public APIs and durable state

- `StaticRelease` is the immutable Git projection of release identity,
  contracts, digests, dependencies and target.
- `ReleaseOperation` is an append-only, immutable request with action, approved
  digest, requester, reason, timeout and idempotency key.
- `ssa-state-<release>` persists accepted digest, exact inventory and operation
  history independently of a transient Actions runner.
- Compatibility HelmRelease/HelmRepository/HelmChart objects are labeled
  `delivery.re8ch.com/compat-projection=true` and expose status only.

The schemas and concrete implementation are in:

- [`clusters/re8ch/components/release-compat/api.yaml`](../../../clusters/re8ch/components/release-compat/api.yaml)
- [`clusters/re8ch/components/release-compat/admission.yaml`](../../../clusters/re8ch/components/release-compat/admission.yaml)
- [`operations/releases/ssa_executor.py`](../../releases/ssa_executor.py)
- [`operations/releases/release-compat-controller/`](../../releases/release-compat-controller/)
- [`operations/releases/operation_materialize.py`](../../releases/operation_materialize.py)

## Supply-chain implementation to copy

1. Lock the renderer, source revision/chart archive, values and every referenced
   image by digest. Publishing must not consult a mutable Helm index.
2. Render in GitHub without Kubernetes write credentials.
3. Normalize identities, reject duplicates and unknown hooks, calculate the
   bundle and inventory digests, and assign every object to a declared stage.
4. Publish to GHCR by immutable digest and sign keylessly with repository,
   workflow and ref provenance.
5. Acquire the artifact-publisher OIDC identity, copy the exact digest into
   Harbor, sign the Harbor target and verify the target signature.
6. Have the promoter App open a signed PR containing only the new approved
   digest. Let normal branch protection and CI merge it.
7. Project the merged digest into `StaticRelease`. Execution accepts only an
   operation ID and rereads the immutable operation from Kubernetes.
8. Split infra, runtime and final acceptance into identities that cannot perform
   each other's privileged work.

Reference implementations:

- [Ceph compiler](../../releases/compile_ceph_static.py)
- [protected Harbor mirror](../../releases/protected_harbor_mirror.py)
- [Ceph promotion](../../releases/promote_ceph.py)
- [controller promotion](../../releases/promote_release_compat.py)
- [Ceph execution workflow](../../../.github/workflows/ceph-static-release.yml)
- [controller release workflow](../../../.github/workflows/release-compat-controller.yml)

## Per-domain migration procedure

### 1. Inventory before writing

Capture, at one timestamp:

- every HelmRelease, Kustomization, source and dependency;
- rendered/live object identities;
- Helm storage history and current revision;
- managedFields owners and conflicts;
- namespace and cluster-scoped RBAC needs;
- controller-generated objects that must stay outside inventory;
- finalizers, prune behavior and protected data/schema objects;
- current Ready/Stalled conditions, events and metrics labels.

Do not use a repository-only HelmRelease inventory for an autonomous domain;
those releases live in private domain repositories. Compare that repository's
locked declarations with a live-cluster snapshot.

### 2. Partition authority

Classify every object as exactly one of:

- chart/Kustomize-owned and safe for SSA inventory;
- operator-generated and excluded;
- protected schema/data object with no automatic prune/rollback;
- intentionally retired with a reviewed delete rule;
- blocked pending evidence.

Create a separate `StaticRelease` for each independently rollbackable unit.
Express ordering in its dependency DAG; do not encode ordering as sleeps.

### 3. Build shadow bundles

For each release:

1. render twice and require byte-identical bundle and inventory output;
2. compare signed inventory with the live Helm/Kustomize inventory;
3. classify every difference;
4. run server-side dry-run with the exact future executor identity;
5. enumerate individual fields needing one-time forced ownership;
6. fail on any undeclared object, mutable image, unknown hook or missing value.

Never use whole-object unconditional force ownership.

### 4. Prove lifecycle before cutover

The first release in a domain must demonstrate:

- ordinary reconcile and idempotent replay;
- harmless executor-owned field drift followed by restoration;
- allowed code-object rollback to the prior signed digest;
- protected-object failure freezing instead of destructive rollback;
- executor restart and GitHub outage without workload or inventory loss;
- exact prune of one disposable test object, if the domain permits prune;
- denied Secret read, Pod exec, TokenRequest, impersonation and out-of-scope
  writes;
- correlated Operation status, Events, managedFields and audit evidence.

### 5. Transfer ownership

1. Record the last healthy Flux revision, inventory and rollback digest.
2. Suspend the exact Flux consumer while leaving its object and history intact.
3. Run the approved SSA takeover operation.
4. Verify health and stability before moving the next dependency.
5. Convert the Flux object to a labeled compatibility projection.
6. Remove the old finalizer only through the reviewed orphan procedure; never
   let deletion uninstall a migrated workload.
7. Keep the old controller running only for remaining real consumers.

### 6. Retire controllers in dependency order

For one autonomous domain, the retirement sequence is:

1. real HelmRelease consumers reach zero;
2. scale/delete that domain's helm-controller and workload-write RBAC;
3. real Kustomization consumers reach zero;
4. scale/delete that domain's kustomize-controller and workload-write RBAC;
5. remove source-controller only after HelmChart/OCIRepository/GitRepository
   consumers reach zero or are replaced by compatibility projections;
6. remove notification-controller only after receiver/provider/event consumers
   reach zero;
7. keep compatibility CRDs until Flux CLI/Headlamp clients no longer require
   them.

Controller deployment count is not proof. The gate is a live consumer query,
no old finalizers, no old controller managedFields writes, healthy replacements,
and a completed rollback drill.

## Flux CLI and Headlamp compatibility

The compatibility controller projects Flux v2 Conditions, revisions, compact
history, source artifacts, Events and metrics. It converts standard
`requestedAt`, `forceAt` and `resetAt` annotations into deterministic
ReleaseOperations.

During coexistence, the old helm-controller can acknowledge `requestedAt` on a
suspended projection without executing Helm. The compatibility controller must
therefore use deterministic ReleaseOperation existence as the idempotency
authority, never `status.lastHandledReconcileAt`. This race was reproduced and
fixed in PR `#2285`.

There is one deliberate transition limitation: the upstream Flux CLI refuses
`flux reconcile helmrelease` locally when `spec.suspend=true`. While the old
helm-controller still watches the namespace, leaving compatibility projections
suspended is the safe dual-writer barrier. Standard annotation requests already
work end to end. Before claiming complete CLI parity, either:

- configure the old helm-controller to exclude compatibility projections and
  make those projections non-suspended; or
- remove the last real consumers and retire the old helm-controller, then make
  projections non-suspended.

Do not unsuspend a projection merely to make the CLI command print success.

After that gate, verify unchanged Flux CLI `get`, `reconcile`, `--with-source`,
`--force`, `--reset`, `suspend` and `resume`, plus the existing Headlamp Flux
plugin. Desired-state spec edits and direct delete must remain denied.

## Failure behavior and rollback rules

- A GitHub, Harbor, metrics, observer or dispatcher outage never triggers prune
  or rollback and does not affect running workloads.
- Expired pending operations terminate as `Failed/expired`; they must not be
  dispatched when a controller restarts.
- Digest, signer, repository, workflow, audience, environment, inventory or
  operation mismatches fail closed before mutation.
- Automatic rollback reapplies the prior signed bundle; it never rerenders old
  source.
- CRDs, namespaces, persistent storage, StorageClasses and data-bearing custom
  resources require an explicit protected rollback policy.
- A failed protected-data change freezes the release and requires a separate,
  approved operation.
- Restoring Flux is an explicit ownership transfer from the recorded revision
  and inventory, not an unsuspend over an active SSA owner.

## Operational lessons from the first cutover

1. **Keep one browser authorization surface.** Creating a GitHub App can require
   human authorization. Do not silently switch from the in-app browser to a
   separate browser and force a second login.
2. **Scratch images still need trust roots.** A Go binary in `FROM scratch`
   could not verify `api.github.com`; copy a current CA bundle into the runtime
   image and test the real GitHub token endpoint.
3. **Separate App creation from installation.** Verify the App permission set,
   repository selection and installation ID independently. `Actions: write`
   plus `Metadata: read` was sufficient; Contents write was unnecessary.
4. **Treat private-key files as one-use bootstrap material.** Validate the
   public fingerprint, create the Secret without printing values, then delete
   the downloaded PEM and prove it is gone.
5. **Admission CEL must handle missing optional fields.** Direct access to an
   absent `dependsOn` caused the validation expression to error and fail closed.
   Compare optional fields using paired `has(...)` checks.
6. **Compatibility status is not an execution ledger.** The retiring controller
   can race on Flux status fields. Immutable operation identity is the durable
   idempotency record.
7. **Controller restarts expose stale work.** Dispatcher startup immediately
   found old pending operations. Timeout/expiry checks must precede dispatch.
8. **Promote binaries by digest through Git.** Fixes are not complete when a PR
   merges; build, sign, mirror, verify, open the activation PR, merge it,
   reconcile Git and wait for rollout.
9. **Validate the entire path, not isolated credentials.** The acceptance proof
   is annotation/operation creation, App dispatch, OIDC authentication, SSA,
   health acceptance and terminal status—not merely obtaining an App token.
10. **Long stages need visible progress and bounded timeouts.** CRD/RBAC
    acceptance took about six minutes in the successful proof. Do not replace a
    legitimate bounded wait with repeated dispatches.
11. **Health is domain-specific.** A CephCluster can be `Ready` with a reported
    warning; the contract must state the exact acceptable quorum, OSD, PG and
    smoke-test facts instead of trusting a single generic status string.
12. **Preserve the GitOps worktree invariant.** All changes used isolated
    worktrees, signed commits, required checks and merged PRs; the main anchor
    remained clean and was verified with workspace doctor after cleanup.
13. **Do not shadow shell-special variables.** In zsh, assigning the array
    variable `path` also changes `PATH`; use task-specific names such as
    `manifest_path` or `candidate_file` in validation and cleanup scripts.

## Evidence ledger

| Evidence | Result |
| --- | --- |
| Dispatcher activation PR `#2281` | merged |
| CA bundle and operation-expiry PR `#2282` | merged |
| First signed-image activation PR `#2283` | merged |
| Optional-field admission fix PR `#2284` | merged |
| Flux acknowledgement-race fix PR `#2285` | merged |
| Final signed-image activation PR `#2286` | merged |
| Controller release workflow `40024973784` | build, test, sign, Harbor copy/sign/verify, activation PR succeeded |
| Ceph compatibility reconcile `40026749978` | infra, runtime and accept succeeded |
| Dispatcher deployment | 1/1 at signed Harbor digest |
| Compatibility controller deployment | 2/2 at signed Harbor digest |
| Workspace doctor after merge | healthy, 62 modules |

## Live inventory at handoff

The 2026-10-24 snapshot contained 29 HelmRelease objects: three Ceph
compatibility projections and 26 real consumers.

Four autonomous domains owned 11 healthy real HelmReleases:

- agent-runtime: one;
- networking: five;
- observability-metering: five;
- identity-access: zero HelmReleases, but one Kustomization.

The central `flux-system` still had 12 real HelmReleases. Its known exceptions
were `advanced-fabric` (`ValuesError`), suspended `public-edge-manager`, and
suspended/progressing `toolhive-operator`. The legacy `observability-system`
namespace contained three suspended `ArtifactFailed` duplicates. These are
cleanup/migration inputs, not reasons to weaken the executor.

The live snapshot is intentionally dated. Every delegated task must refresh it
with:

```sh
kubectl get helmreleases.helm.toolkit.fluxcd.io -A -o json
kubectl get kustomizations.kustomize.toolkit.fluxcd.io -A -o json
kubectl get gitrepositories,ocirepositories,helmrepositories,helmcharts -A
kubectl get deploy -A -l app.kubernetes.io/part-of=flux
```

The main-domain static cohort is already described in
[`operations/releases/main-domain-ssa/cohort.yaml`](../../releases/main-domain-ssa/cohort.yaml):
17 releases, 124 objects and five source domains. Each source domain has its
own protected environment, OIDC audience and Kubernetes group in
[`operations/releases/static-domain-authority.yaml`](../../releases/static-domain-authority.yaml).
Split those domains by authority and rollback boundary; do not create one new
main-domain super-owner. The shared `release-main-domain` environment and
`release-executor-main-domain` identity are retirement inputs, not the target.

## Required completion report from every delegated task

Each task must return:

1. merged PR and exact main SHA;
2. signed source and Harbor bundle digests;
3. StaticRelease and ReleaseOperation identities;
4. OIDC audiences, environments and RBAC denial evidence;
5. live-versus-bundle inventory classification;
6. SSA conflict and any approved force-field list;
7. drift, rollback, prune and GitHub-outage results;
8. health evidence and stability-window timestamps;
9. remaining real HelmRelease/Kustomization/source consumers;
10. removed finalizers/controllers/RBAC, or the exact blocker preventing removal.

Do not report a domain complete because its manifests render or its PR merges.
Completion means the signed bundle is the only workload writer, failure behavior
has been exercised, compatibility clients still work, and the obsolete Flux
controllers have no remaining consumers.
