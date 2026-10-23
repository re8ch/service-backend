# Task: build the unified immutable-bundle SSA executor

## Implementation status

The Ceph production cohort completed the first end-to-end implementation on
2026-10-24. Three Helm releases now execute as signed OCI bundles through
audience-specific infra/runtime identities, bounded SSA, durable inventory and
health acceptance. The independent `re8ch-release-dispatcher` GitHub App and
Flux compatibility controller are live, and a compatibility request completed
as `ReleaseOperation/rook-ceph-9cd3f4260b72b501` through Actions run
`40026749978`.

The implementation evidence, operational lessons, repeatable cutover procedure
and dispatch plan for the four autonomous domains plus the five main-domain
splits are maintained in
[`../static-release-domain-handoff.md`](../static-release-domain-handoff.md) and
[`../static-release-domain-dispatch.yaml`](../static-release-domain-dispatch.yaml).
The remaining work is domain expansion and controller retirement; the shared
executor contract described below remains authoritative.

## Outcome

Provide one minimal executor that can take ownership of qualified Helm and
Kustomize workloads. GitHub performs source resolution, Helm/Kustomize rendering,
policy and approval; the executor accepts only an approved signed bundle digest
and performs bounded runtime reconciliation.

This task is developed together with
[the HelmRelease transition task](wave-4-helmrelease-transition.md). It must not
create separate Helm and Kustomize execution paths. Both converge on the same
`ReleaseContract`, bundle format, inventory, SSA field manager rules, health
model, rollback record and audit envelope.

## Non-goals

- A general-purpose GitHub-to-cluster shell or `kubectl` gateway.
- AI deciding or executing privileged changes.
- Moving Secret plaintext, node inventory or private topology into public
  repository variables, logs or GitHub Issues.
- Reimplementing Git polling, chart rendering or Kustomize building in cluster.
- Removing Flux before each domain proves cutover, drift recovery and rollback.

## Authority model

```text
Git main + reviewed approval
        |
        v
GitHub static compiler -----> signed OCI bundle + provenance
        |                                  |
        |                                  v
        +-----------------------> approved ReleaseContract
                                           |
                                           v
                                  minimal SSA executor
                                           |
                              verify / apply / observe / rollback
                                           |
                                           v
                                      Kubernetes API
```

- Git is desired-state authority.
- The signed bundle digest is the only executable release payload.
- Kubernetes is runtime authority.
- Observer evidence reports state but cannot authorize execution.
- GitHub Projects and Issues govern work; their state never grants Kubernetes
  permission.
- AI may propose a contract change but cannot approve or execute it.

## Execution contract

The executor accepts a typed operation containing at least:

- stable release and target identifiers;
- approved bundle digest and signature/provenance requirements;
- exact namespace and Kubernetes resource allowlist;
- dedicated SSA field-manager identity;
- expected inventory digest;
- health rules and timeout;
- prune policy and deletion allowlist;
- previous approved bundle digest;
- GitHub repository, workflow, commit, run and operation identifiers;
- expiry and replay-protection material.

It rejects arbitrary manifests, mutable tags, raw command strings, undeclared
objects, unapproved namespaces, unknown signers, stale operations and bundles
whose inventory differs from the contract.

## Deployment decision

Implement the contract so transport is replaceable:

- The initial executor may use GitHub-hosted Actions OIDC with tightly scoped,
  per-domain Kubernetes RBAC.
- A later credential broker or small pull executor may issue/consume the same
  operation contract without changing release semantics.
- No mode receives cluster-admin, Secret readback, TokenRequest,
  impersonation, pod exec/log/proxy or unrestricted delete rights.

Direct OIDC execution is not permission to expose a general Kubernetes writer.
Each domain receives a separate identity, environment gate, workflow reference,
resource allowlist and audit group.

## Required capabilities

### Signature and digest verification

- Verify bundle digest before parsing or applying content.
- Verify signer identity, repository/workflow provenance and approval policy.
- Pin all referenced images and nested artifacts by digest.
- Store no private signing key in the cluster or repository.

### Bounded Server-Side Apply

- Use one stable field-manager name per release authority.
- Restrict group, kind, namespace and object names from the approved inventory.
- Reject cluster-scoped resources unless a separately reviewed contract permits
  the exact identity.
- Dry-run first and surface field conflicts.
- Require explicit approval for any force-ownership exception.

### Exact inventory and safe prune

- Persist the last accepted inventory digest independently of the transient
  GitHub job.
- Compute prune as `previous accepted inventory - new approved inventory`.
- Delete only identities explicitly covered by both the old ownership record and
  the new operation's deletion policy.
- Never prune on bundle fetch, verification, admission, GitHub or observer
  failure.
- Treat namespaces, CRDs, persistent storage and security boundaries as
  protected resources requiring separate deletion policy.

### Health and rollback

- Evaluate declared readiness facts until a bounded timeout.
- Record the applied and healthy digest separately.
- On failure, reapply the previous approved bundle; rollback never means render
  the old source again.
- Preserve evidence when rollback fails and stop further automatic operations.

### Audit correlation

- Add the GitHub operation ID and release ID to Kubernetes audit-visible request
  metadata where supported and to managed object annotations where policy allows.
- Emit a redacted result envelope containing the request identity, approved
  digest, observed digest, inventory digest, timestamps and health outcome.
- Evidence must exclude Secret data, tokens, private addresses and node details.

### GitHub outage behavior

- Running workloads and the last accepted inventory remain untouched.
- No lease expiry or missing heartbeat triggers prune or rollback.
- Recovery resumes from the persisted accepted digest and rejects replayed or
  out-of-order operations.

## Work plan

### 1. Freeze the shared schemas

Define and validate:

- `ReleaseContract`;
- bundle manifest and deterministic archive format;
- provenance and signer policy;
- operation request/result envelope;
- accepted-state and inventory record;
- health, prune and rollback policy.

The same schemas must support a Helm-rendered or Kustomize-rendered bundle. The
executor must not know which renderer produced it.

### 2. Build the GitHub static compiler

Create pinned GitHub-hosted workflows that:

1. resolve locked inputs;
2. render Helm or Kustomize;
3. normalize and validate objects;
4. enforce namespace/resource/Secret policy;
5. compute inventory and rendered digest;
6. publish and sign the OCI bundle;
7. attach provenance and Checks;
8. produce an execution request only after required approval.

Compiler jobs have no write access to Kubernetes. The execution job does not
receive source credentials or GitHub App private keys.

### 3. Implement the constrained executor

Implement only the typed verbs required by the operation catalog: verify,
dry-run, apply, observe, prune and rollback. Each verb checks the same contract
and authorization envelope. Persist accepted state before acknowledging success.

The executor must be idempotent. Repeating the same operation ID and digest
returns the recorded result; a reused operation ID with different content is
rejected.

### 4. Shadow current reconciliation

For selected HelmRelease and Kustomization candidates:

- compile bundles without write permission;
- compare inventory, normalized manifests and expected field ownership;
- run server-side dry-run with the executor identity;
- report conflicts and unsupported lifecycle behavior;
- leave Flux as the sole writer.

Shadow parity does not authorize cutover.

### 5. Cut over one low-risk domain

1. Record the last healthy Flux revision, inventory and rollback path.
2. Suspend the relevant Flux object without deleting it.
3. Execute the approved bundle and verify audit/evidence correlation.
4. Prove drift recovery by changing one harmless executor-owned field through a
   controlled test and reconciling it from the same digest.
5. Prove health-timeout rollback.
6. Observe a stability window with no dual writer.
7. Use the object-specific orphan/finalizer procedure before removing the Flux
   CR.

Networking, identity, storage, Secret controllers and cluster-wide guardrails
must not be the first domain.

### 6. Expand and retire Flux by domain

For every domain, require:

- all Helm releases classified under Wave 4;
- all Kustomizations mapped to immutable bundles;
- zero unresolved SSA conflicts;
- exact prune and rollback inventories;
- successful outage and recovery drills;
- no remaining Flux finalizer or source consumer.

Only then remove that domain's Helm/Kustomize objects and reduce its Flux
controllers. `source-controller` and shared CRDs are removed last, after their
consumer count reaches zero.

## Security tests

- Invalid signer, digest, repository, workflow, ref, environment, audience,
  operation ID or expiry is rejected.
- A valid identity cannot escape its namespace or resource allowlist.
- Secret get/list/watch, pod exec/log/proxy, TokenRequest and impersonation are
  denied.
- Undeclared create, patch, delete and force ownership are denied.
- Bundle/path traversal, duplicate object identity and oversized input are
  rejected before apply.
- Logs and evidence are scanned for credentials and restricted topology.

## Reconciliation tests

- Applying the same operation twice is idempotent.
- Conflicting managed fields fail without mutating live ownership.
- Inventory survives job and GitHub outages.
- Prune deletes exactly the approved stale set and nothing else.
- Health timeout restores the previous accepted digest.
- Rollback failure stops progression and creates one deduplicated incident.
- Observer independently confirms applied digest and health.
- Helm and Kustomize candidates produce the same execution envelope shape.

## Rollback and break glass

- Disable the relevant execution environment and OIDC authorization.
- Reapply the previous accepted bundle when the executor remains trustworthy.
- If executor identity or bundle integrity is suspect, use the separately stored
  emergency-admin path and preserve audit evidence.
- Restore Flux only from the recorded source revision and inventory, with an
  explicit SSA ownership transfer; do not simply unsuspend it over an active
  executor.
- Never use a new public endpoint, host route or long-lived kubeconfig as a
  rollback shortcut.

## Completion criteria

- One executor contract handles qualified Helm and Kustomize bundles.
- Every operation is signed, digest-bound, allowlisted, idempotent and auditable.
- Inventory, prune, health and rollback survive GitHub unavailability.
- No AI, Observer, Issue or Project state can grant execution authority.
- Every migrated domain passes drift recovery, rollback and outage drills.
- Flux Kustomizations are deleted only after finalizer and field ownership are
  cleanly transferred.
- Flux controllers are reduced only when live consumer counts prove them idle.

