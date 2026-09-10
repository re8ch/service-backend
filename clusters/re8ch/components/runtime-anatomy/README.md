# Runtime Anatomy

Status: CephFS file-state canary MVP. The chart installs six alpha CRDs, one
controller, a labelled canary PVC/runtime, and the final scope ConfigMap.

The initial delivery is the
[`runtime-anatomy`](../../charts/runtime-anatomy) Helm chart. It publishes one
final `RuntimeScope` document as a ConfigMap and provisions only an explicitly
labelled, isolated canary PVC. The controller refuses sources outside that
boundary. It creates native CephFS `.snap` commits, computes manifest diffs,
restores into new PVCs, and keeps GC permanently in `DryRun`.

The native `.snap` adapter requires the CSI node identity to retain its normal
`allow rw` MDS capability plus `allow rws fsname=re8ch-files path=<bound canary
subvolumePath>`. Reconcile and verify that path-scoped grant with
`operations/scripts/configure-runtime-anatomy-cephfs-snap-caps.sh`. The script
fails closed on an unexpected driver, subvolume path or existing cap set;
`--rollback` removes only the canary-specific `rws` grant.

Runtime Anatomy is a Kubernetes-native framework for describing, observing and
recovering the persistent state of a runtime over time. It does not implement a
filesystem, object store, CSI driver, container runtime, browser or secret
store. It coordinates those systems and records an immutable, content-addressed
account of what state existed, how it was captured, whether it is still valid,
and how it can be restored.

The central abstraction is a **Runtime Commit**: an immutable manifest that
references component-specific artifacts and observations at a bounded point in
time. Commits form a directed acyclic graph (DAG), so operators and higher-level
agents can snapshot, diff, branch, checkout, restore and garbage-collect runtime
state without pretending that every backend has Git semantics or supports an
atomic snapshot.

## 1. Problem statement

A useful runtime is more than a Pod or a PVC. Its behavior may depend on:

- processes and in-memory state;
- container writable layers and mounted filesystems;
- CSI volumes, CephFS subvolumes, S3 objects and node-local caches;
- network identity, policy and dependency reachability;
- browser cookies, LocalStorage, IndexedDB and download artifacts;
- credential references and the versions of injected capabilities;
- external databases, APIs, queues, images and configuration revisions.

Kubernetes exposes these through different APIs with different consistency,
lifecycle and restore guarantees. Runtime Anatomy provides one state model over
them while preserving their real ownership boundaries.

The framework must answer:

1. What components make up this runtime now?
2. Which bytes and identities are durable, and where are they stored?
3. Which observations support the claim that a commit is healthy and restorable?
4. What changed between two points in time?
5. What would checkout or restore do before any mutation occurs?
6. Which artifacts are still reachable and which can be safely collected?

## 2. Non-goals

Runtime Anatomy does not:

- replace Ceph, S3, CSI, CRI, CRIU, CNI, Git, a browser runtime or a credential
  broker;
- claim global atomicity when the underlying systems cannot provide it;
- store bulk snapshot data, memory images or secrets in Kubernetes API objects;
- infer application consistency from a crash-consistent storage snapshot;
- restore credentials by copying secret values into a commit;
- preserve live TCP connections, device memory or kernel state unless a runtime
  adapter explicitly proves support;
- turn disposable caches into durable truth;
- expose Ceph daemon addresses or storage internals as a product API.

## 3. Design principles

### Evidence, not optimism

Every status claim is backed by observations with timestamps, digests, adapter
versions and source identities. Unknown is a first-class result and is never
silently converted to healthy.

### A commit is a manifest, not necessarily one physical snapshot

A commit contains component captures taken inside a declared consistency
window. It may reference a CSI `VolumeSnapshot`, a CephFS subvolume snapshot, an
S3 version manifest, a CRI checkpoint archive or only an observation. The commit
is sealed only after its manifest is immutable and its required artifacts pass
verification.

### Separate capture from restore

The ability to observe or capture a component does not imply that it can be
restored. Every component advertises distinct `observe`, `capture`, `diff`,
`materialize`, `restore` and `delete` capabilities.

### Fencing before writable restore

Checkout never creates two writers for the same mutable state. Volume,
filesystem and browser-profile adapters must acquire an exclusive lease and a
monotonic fencing token before materializing writable state.

### Control plane metadata stays small

CRDs hold desired state, phase, summaries and content digests. Detailed
manifests and artifacts live in an OCI registry or versioned object storage.
Kubernetes object status contains references, not unbounded file lists.

### Degrade explicitly

A runtime can produce a useful commit even if some components are observational
only, provided the requested policy allows it. The resulting commit records its
actual consistency and restorability; it is never mislabeled as complete.

## 4. Conceptual model

```text
Runtime
  │ owns desired component graph
  ├── ProcessSet
  ├── MemoryState
  ├── FilesystemTree
  ├── VolumeSet
  ├── NetworkContext
  ├── BrowserProfile
  ├── CredentialBinding
  └── ExternalDependencySet
          │
          ▼ observe / quiesce / capture / verify
RuntimeCommit (immutable manifest, content digest)
  ├── parent commit(s)
  ├── consistency window and level
  ├── component records → artifact references
  ├── provenance and policy result
  └── health / freshness / validity / durability / restorability
          ▲
          │ mutable compare-and-swap pointer
RuntimeBranch

RuntimeCheckout → RuntimeRestorePlan → fenced materialization → validation
```

The DAG supports one parent for normal commits and multiple parents for a
metadata merge. A merge does not merge opaque memory images or filesystem
snapshots. It is valid only when a planner can select or synthesize exactly one
restorable version for every required component and report conflicts.

### Runtime identity versus incarnation

A `Runtime` is the stable logical identity. A `RuntimeInstance` is one running
incarnation on a particular provider, Pod, VM or microVM. A restore creates a new
incarnation unless an adapter proves safe in-place restore. Commit identity is
independent of the source incarnation.

### State classes

| Class | Examples | Default treatment |
| --- | --- | --- |
| Authoritative durable | CephFS workspace, database backup, browser profile | Required capture and restore verification |
| Reconstructable | image, declared Pod spec, package cache | Record digest and reconstruction recipe |
| Ephemeral | PID, live socket, tmpfs, node cache | Observe; capture only with explicit adapter support |
| External | SaaS API, database, queue, DNS | Record identity/version/watermark; never imply ownership |
| Sensitive | credential binding, memory image, browser cookies | Reference only, encrypt artifacts, restrict restore |

## 5. Consistency model

Each commit declares one achieved level:

| Level | Meaning |
| --- | --- |
| `Observed` | Inventory and health evidence only; no durable capture promised |
| `CrashConsistent` | Artifacts resemble an abrupt stop; buffered application state may be absent |
| `FilesystemConsistent` | Filesystems were frozen/flushed or snapshotted under an adapter-defined barrier |
| `ApplicationConsistent` | Application hooks acknowledged quiesce and post-capture resume |
| `Coordinated` | All required components crossed the same logical barrier within the allowed skew |

`Coordinated` is not automatically stronger than `ApplicationConsistent`; it is
an additional multi-component property. The manifest records both the component
consistency and the runtime-wide coordination result.

The commit's capture window is `[startedAt, completedAt]`. Policy sets
`maxCaptureSkew`; the controller records the earliest and latest component
barrier timestamps. If skew exceeds policy, the commit may remain usable but
cannot claim coordinated consistency.

## 6. Core objects

### Runtime

Namespaced desired-state root. It selects or explicitly lists runtime
components, default commit policy and the branch to track.

Important fields:

- `spec.subjectRef`: Pod, Job, TerminalDevice, BrowserDevice, VM or provider ID;
- `spec.components[]`: stable component name, kind, adapter and source reference;
- `spec.commitPolicy`: required components, consistency target and hooks;
- `spec.retentionPolicyRef`;
- `status.instanceRef`, `status.headCommit`, conditions and aggregate indicators.

### RuntimeCommit

Namespaced request plus immutable result. Before sealing, its spec describes the
requested cut. Once `status.phase=Sealed`, `status.manifest.digest` is the commit
identity and referenced component records cannot be mutated. Corrections produce
a new commit.

A commit contains:

- zero, one or more parent digests;
- runtime UID and source incarnation UID;
- requested and achieved consistency;
- capture window and barrier ID;
- component artifact/observation records;
- schema, adapter and platform versions;
- policy evaluation and aggregate indicators;
- an integrity signature or attestation reference.

### RuntimeBranch

A mutable named pointer to one sealed commit. Updates require `expectedHead`,
providing compare-and-swap behavior. Branches are GC roots. Tags can be modeled
as immutable branches.

### RuntimeCheckout

A declarative request to inspect or materialize a commit. Modes:

- `Plan`: produce actions, compatibility findings and risk without mutation;
- `ReadOnly`: mount or expose immutable artifacts for inspection;
- `Restore`: create a new runtime incarnation from the selected commit;
- `Promote`: after validation, move a branch head to the restored incarnation's
  new commit.

### RuntimeRestorePlan

Planner output frozen by digest. It specifies ordering, fencing, target classes,
capacity, adapters, transformations, rollback steps and validation gates. An
executor rejects a plan if runtime generation, source commit, capability set or
target inventory changed since planning.

### RuntimeObservation

A bounded evidence envelope emitted by observers. High-cardinality samples go
to the telemetry backend; the CRD keeps the latest summary and evidence URI.
Observations are signed or attributable to an observer identity.

### RuntimeRetentionPolicy

Defines branch/tag roots, minimum age, maximum count/bytes, legal holds,
component-specific expiry and deletion grace. It also declares whether backend
snapshots may be deleted when the corresponding commit becomes unreachable.

## 7. Component record

All component adapters normalize results into this envelope:

```yaml
name: workspace
kind: Filesystem
adapter: cephfs.runtime-anatomy.re8ch.com/v1alpha1
sourceRef:
  apiVersion: v1
  kind: PersistentVolumeClaim
  name: terminal-home
capture:
  mode: Snapshot
  consistency: FilesystemConsistent
  artifactRef:
    mediaType: application/vnd.re8ch.runtime-component.v1+json
    uri: oci://registry.example/runtime-anatomy/components@sha256:...
    digest: sha256:...
  backendRef:
    type: VolumeSnapshot
    name: ra-01j...
observedAt: 2026-09-09T08:00:00Z
indicators:
  health: Healthy
  freshness: Fresh
  validity: Valid
  durability: Replicated
  restorability: Verified
provenance:
  sourceUID: 7ee0...
  resourceVersion: "1920234"
  adapterImageDigest: sha256:...
  nodeName: restricted-node-6dc3f140f9
```

Artifact URIs are opaque references. Credentials required to read them are
resolved at execution time through workload identity or a credential broker.

## 8. Indicators

Indicators are orthogonal dimensions, not one ambiguous health score.

### Health

Whether the component currently performs its intended function. Sources include
Pod readiness, CSI operations, Ceph health, mount/read probes, browser process
status and dependency checks.

Values: `Healthy`, `Degraded`, `Unhealthy`, `Unknown`.

### Freshness

How far the captured or observed state lags the declared source. Expressed as a
timestamp/watermark and evaluated against policy, not just a label.

Values: `Fresh`, `Stale`, `Expired`, `Unknown`.

### Validity

Whether the state is internally and semantically acceptable: manifest digest
matches, artifact is readable, required files exist, browser profile format is
compatible, application hook completed, and dependencies satisfy constraints.

Values: `Valid`, `Invalid`, `Unverified`, `Unknown`.

### Durability

Expected survival across failure domains. This derives from evidence such as
replica policy, backend status, object version/retention, and cache placement.
It must not equate a `Bound` PVC with durable data.

Values: `Ephemeral`, `NodeLocal`, `Replicated`, `Remote`, `Unknown`, plus a
structured list of failure domains and evidence.

### Restorability

Whether a compatible target and tested procedure can materialize the component.
`Verified` requires a restore test newer than the policy threshold, not merely a
successful snapshot.

Values: `Unsupported`, `Plannable`, `Verified`, `Blocked`, `Unknown`.

### Provenance

Who or what produced the state and through which chain: source UID/version,
parent commit, adapter image digest, backend object version, node/runtime
versions, timestamps and attestations. Provenance is structured evidence rather
than a pass/fail score.

### Aggregation

Runtime-level values are policy-derived. Required components normally use the
weakest-link result; optional components are reported separately. The API must
retain per-component values so an aggregate never hides the cause.

## 9. Component mappings

### Process and memory

Kubernetes supplies Pod/container identity and status. CRI supplies runtime
introspection; the kubelet checkpoint API can request a container checkpoint
when the installed runtime supports it. Checkpoint archives are sensitive
because they can contain memory-resident credentials.

Runtime Anatomy adds capability discovery, quiesce coordination, encrypted
artifact publication, compatibility metadata, restore planning and verification.
For the initial MVP, Process and Memory are observational: process inventory,
image/runtime digests, restart counts and checkpoint capability are recorded,
but live-memory restore is not promised.

Pod-level checkpoint/restore must remain an experimental adapter until the
target Kubernetes and CRI implementation expose a stable, verified restore
path. Volumes, shared memory, devices and network connections must be modeled as
separate components even when a process checkpoint exists.

### Filesystem and volume

Kubernetes supplies PVC/PV binding and CSI `VolumeSnapshot` resources when the
driver supports snapshots. Rook/Ceph supplies CephFS/RBD lifecycle, replication
and backend snapshot behavior.

Runtime Anatomy adds:

- source-to-snapshot identity and barrier membership;
- mount/read/write and backend-health evidence;
- requested versus achieved consistency;
- restore recipes, target `StorageClass` constraints and post-restore probes;
- a distinction between volume control-plane health, data-path health and
  recoverability.

A CephFS subvolume snapshot and a CSI `VolumeSnapshot` are adapter-specific
backend references; neither becomes the Runtime Commit itself.

Volume Health is evaluated as a vector rather than a single PVC phase:

| Plane | Example evidence | Failure interpretation |
| --- | --- | --- |
| Declaration | PVC/PV binding, access mode, reclaim policy, generation | Desired storage is absent or incompatible |
| Control | CSI controller readiness, snapshot readiness, provisioning latency | New capture/restore may fail while existing mounts still work |
| Data path | node-stage/publish state, mount probe, read/write/fsync probe | Runtime cannot reliably access bytes |
| Backend | Ceph quorum, MDS/OSD/pool state, S3 version readability | Stored state may be degraded or unavailable |
| Capacity | quota, free space, inode/object limits, near-full trend | Capture or continued execution may exhaust storage |
| Protection | replicas/failure domains, snapshot age, retention, encryption | Data exists but may not survive the required failure |
| Recovery | last successful clone/restore and validation age | Snapshot exists but recoverability is unproven |

Observers report these planes separately. For example, an already-mounted
CephFS workspace can have a healthy data path while the CSI control plane is
unable to create a new subvolume. In that state runtime health may remain
`Degraded`, but new commits or restores that need provisioning are `Blocked`.

### S3/object state

Object storage is not treated as a POSIX snapshot. An adapter records a stable
manifest of bucket, prefix, object key, version ID/ETag, size and checksum. A
commit is valid only if the required versioned objects or immutable copied
objects remain readable. Listing time and continuation boundaries are part of
the capture window.

If bucket versioning is unavailable, the adapter may copy to a content-addressed
immutable prefix. Otherwise it can only report `Observed` or
`CrashConsistent`, according to policy. The commit never embeds access keys.

### Local cache

Node-local caches are reconstructable or ephemeral by default. The record stores
cache key, source digest, node identity, size, last verification and hydrate
recipe. A cache cannot raise runtime durability and should normally be excluded
from restore requirements.

### Browser profile

A browser profile is an application-state component, not merely a directory.
It includes filesystem data plus browser family/version, schema compatibility,
clean-shutdown evidence and an exclusive-writer lease.

Capture sequence:

1. fence new side-effecting sessions;
2. request browser flush/clean shutdown when supported;
3. capture the profile volume or immutable object manifest;
4. record browser/version compatibility and validate the artifact;
5. resume the source only after the cut is sealed or explicitly abort it.

Checkout creates a new profile incarnation with a new fencing token. Terminals
receive a short-lived browser endpoint capability; they do not receive raw
cookie files. In the first integration, SteelBrowser remains owner of browser
process/session lifecycle while Runtime Anatomy owns commit coordination and
evidence.

### Credentials

Only `CredentialBinding` metadata is committed: broker reference, audience,
scope digest, issuer, policy version and renewable/not-after information. Secret
values, tokens and private keys are never captured intentionally.

Restore re-resolves bindings and injects fresh credentials. A commit may become
invalid or blocked if the binding was revoked or its policy no longer permits
the target. Process or browser artifacts that may contain credentials require a
sensitive-artifact class, encryption, dedicated authorization and expiry.

### Network and external dependencies

Network state records declared Services, DNS names, NetworkPolicies, routes,
egress class and observed reachability. It does not promise restoration of live
connections or IP identity.

External dependencies are recorded as contracts and watermarks: endpoint
identity, API/schema version, database backup/LSN where available, image digest,
queue offset, health and observation time. Restore planning revalidates them and
may require a migration or block checkout.

## 10. Architecture

```text
                         Kubernetes API
                              │
                ┌─────────────┴─────────────┐
                │ Runtime Anatomy Controller│
                │ reconciliation + FSM      │
                └──────┬───────────┬────────┘
                       │           │
              ┌────────▼───┐   ┌───▼──────────┐
              │ Planner     │   │ Commit/GC    │
              │ dry-run DAG │   │ coordinator  │
              └──────┬─────┘   └───┬──────────┘
                     │             │
        ┌────────────┴─────────────┴─────────────┐
        │ Adapter interface / capability registry│
        └──┬──────┬──────┬──────┬──────┬────────┘
           │      │      │      │      │
          CSI    CRI   CephFS   S3   Browser/Credential
           ▲      ▲      ▲      ▲      ▲
           └──────┴──────┴──────┴──────┴── Observers
                                      │
                     Metrics / traces / evidence store
```

### Controller

Owns Kubernetes reconciliation, finalizers, leases, status conditions and the
commit/checkout state machines. It never shells into storage systems directly;
it invokes registered adapters through typed jobs or a narrow service API.

### Observer

Continuously collects inventory, capability and health evidence independently
of commit requests. Observers publish normalized summaries and telemetry. This
separation lets a failed capture be diagnosed without making capture itself the
health probe.

### Planner

Builds a dependency DAG and performs a no-mutation preflight. It selects an
adapter for every component, checks capabilities and compatibility, calculates
capture order or restore order, identifies destructive/replacement actions,
defines rollback points and emits a digest-bound `RuntimeRestorePlan`.

Typical restore order is durable storage, filesystem/profile materialization,
credential re-resolution, network policy/services, process/runtime creation,
dependency checks, then application validation. Actual ordering is derived from
component dependencies rather than hard-coded by kind.

### Adapter contract

Conceptual interface:

```text
Discover(source) -> Capabilities
Observe(source) -> Observation
PrepareCapture(source, barrier) -> PrepareResult
Capture(source, barrier) -> ComponentRecord
Verify(record) -> Indicators
Diff(base, target, mode) -> ComponentDiff
PlanRestore(record, target) -> Actions + Constraints
Materialize(record, target, fence) -> MaterializedRef
Validate(materializedRef) -> Indicators
Delete(record, gcToken) -> DeletionResult
```

Calls are idempotent by operation ID. Adapter outputs are content-digested.
Destructive deletion requires a GC authorization token scoped to an unreachable
artifact and is retried until backend confirmation is recorded.

## 11. State machines

### Commit

```text
Pending → Discovering → Quiescing → Capturing → Verifying → Sealed
             │              │           │            │
             └──────────────┴───────────┴────────────┴→ Failed
                                           └──────────→ Degraded → Sealed
```

- `Pending`: request accepted and policy/defaults resolved.
- `Discovering`: component graph and adapter capabilities frozen.
- `Quiescing`: leases acquired and application/filesystem barriers entered.
- `Capturing`: component artifacts created; partial results remain unsealed.
- `Verifying`: digests, availability and policy are evaluated.
- `Degraded`: optional failure or weaker consistency requires explicit policy.
- `Sealed`: immutable manifest stored and branch update attempted with CAS.
- `Failed`: source is unquiesced; partial artifacts enter short-lived GC grace.

### Checkout/restore

```text
Pending → Planning → AwaitingApproval? → Fencing → Materializing
   → Rebinding → Starting → Validating → Ready
           │          │          │           │
           └──────────┴──────────┴───────────┴→ RollingBack → Failed
```

Approval is policy-driven, not mandatory for read-only planning. Any writable
restore fences the previous writer before `Materializing`. `Ready` means all
required validation gates passed; a merely running Pod is insufficient.

### Garbage collection

```text
ReachabilityScan → Candidate → GracePeriod → Deleting → Collected
                        ▲             │           └→ DeleteBlocked
                        └─────────────┴── new reference cancels deletion
```

GC is mark-and-sweep over branch heads, immutable tags, active checkouts, legal
holds and explicitly pinned commits. Backend deletion happens only after the
grace period and a second reachability check. `Retain` storage policy is honored;
such artifacts are reported as orphan candidates rather than deleted.

## 12. Declarative API sketch

Proposed group: `anatomy.re8ch.com/v1alpha1`.

```yaml
apiVersion: anatomy.re8ch.com/v1alpha1
kind: Runtime
metadata:
  name: agent-harry
  namespace: ai-operations
spec:
  subjectRef:
    apiVersion: workspace.re8ch.com/v1alpha1
    kind: TerminalDevice
    name: harry-terminal
  components:
    - name: workspace
      kind: Filesystem
      adapterRef: {name: cephfs}
      sourceRef: {apiVersion: v1, kind: PersistentVolumeClaim, name: terminal-home}
      required: true
    - name: browser-profile
      kind: BrowserProfile
      adapterRef: {name: steel-browser-profile}
      sourceRef: {apiVersion: workspace.re8ch.com/v1alpha1, kind: BrowserProfile, name: harry}
      dependsOn: [workspace]
      required: true
    - name: process
      kind: Process
      adapterRef: {name: kubernetes-cri}
      required: false
  commitPolicy:
    consistency: ApplicationConsistent
    maxCaptureSkew: 30s
    quiesceTimeout: 60s
  retentionPolicyRef: {name: interactive-runtime-default}
---
apiVersion: anatomy.re8ch.com/v1alpha1
kind: RuntimeCommit
metadata:
  generateName: agent-harry-
  namespace: ai-operations
spec:
  runtimeRef: {name: agent-harry}
  parents: [sha256:previous]
  reason: before-upgrade
  branchRef: {name: main, expectedHead: sha256:previous}
  consistency: ApplicationConsistent
---
apiVersion: anatomy.re8ch.com/v1alpha1
kind: RuntimeCheckout
metadata:
  name: inspect-before-upgrade
  namespace: ai-operations
spec:
  runtimeRef: {name: agent-harry}
  commitRef: {digest: sha256:commit}
  mode: Plan
  target:
    namespace: ai-operations-restore
```

Status uses Kubernetes conditions such as `Discovered`, `Quiesced`, `Captured`,
`Verified`, `Restorable`, `Fenced`, `Materialized` and `Ready`. Conditions include
`observedGeneration`, stable reason codes and evidence references.

The API server should validate syntax and immutability; policy admission checks
adapter allowlists, source namespace access, sensitive artifact rules and
restore authorization. Semantic capability checks remain asynchronous.

## 13. Snapshot, diff, branch, checkout and restore semantics

### Snapshot/commit

`RuntimeCommit` is the user-facing snapshot operation. Sealing computes the
canonical manifest digest. A commit can be incomplete only if policy explicitly
allows missing optional components; the manifest lists every omission.

### Diff

Diff modes are component-specific:

- `Metadata`: identity, version, size, health and configuration;
- `Manifest`: file/object key and digest changes when manifests exist;
- `Semantic`: adapter-defined changes such as browser version, dependency schema
  or credential scope;
- `BinaryOpaque`: artifact digest changed, details unavailable.

Runtime Anatomy does not download memory archives or entire volumes merely to
produce a default diff.

### Branch

Branches are cheap pointers. Creating a branch does not clone data. Divergent
writes occur only after checkout materializes distinct writable state. Moving a
branch uses CAS to prevent lost updates.

### Checkout versus restore

Checkout resolves and plans a commit. Restore performs the plan. Read-only
checkout may expose snapshots directly when safe. Writable checkout always
creates or clones a new mutable incarnation; it never writes into an immutable
snapshot.

### Restore

Restore success requires component validation and runtime-level probes. A plan
may restore only a subset for forensic inspection, but it must label that result
as partial and must not promote it as the branch head of a complete runtime.

## 14. Responsibility boundary

| Capability | Existing primitive/provider | Runtime Anatomy responsibility |
| --- | --- | --- |
| Pod desired state and status | Kubernetes API/controllers | Bind objects into one runtime graph |
| Container execution | kubelet + CRI + OCI runtime | Capability discovery, coordination, evidence |
| Container checkpoint request | kubelet/CRI when supported | Secure artifact lifecycle and restore planning |
| Volume provisioning | CSI/PV/PVC/StorageClass | Classify, observe and select restore targets |
| Volume snapshots | CSI snapshot controller/driver | Barrier membership, verification and commit reference |
| CephFS/RBD durability | Rook/Ceph | Interpret backend evidence; never operate as storage engine |
| Object versions/copies | S3-compatible provider | Immutable manifest, checksum and retention linkage |
| Local cache | node/cache implementation | Mark reconstructable; record hydrate recipe |
| Browser sessions | SteelBrowser/browser provider | Profile fencing, capture coordination, compatibility evidence |
| Credential values | Credential Broker/Secret provider | Commit only binding metadata and re-resolve on restore |
| Network enforcement | Cilium/Kubernetes NetworkPolicy/Service | Record intent/evidence and validate restored reachability |
| Telemetry storage | Prometheus/OTel/log store | Define normalized indicators and evidence linkage |
| Timeline and GC | none across all components | Commit DAG, branch CAS, reachability and policy |

## 15. Minimal MVP

The MVP proves the state model without requiring memory restore.

### Scope

1. Install only the Runtime, RuntimeCommit, RuntimeBranch, RuntimeCheckout and
   RuntimeRetentionPolicy CRDs plus one controller.
2. Integrate an existing `TerminalDevice` and SteelBrowser `BrowserProfile` as
   runtime subjects; do not replace their lifecycle controllers.
3. Implement Kubernetes inventory, CephFS/CSI filesystem and SteelBrowser
   profile adapters.
4. Store canonical commit manifests in a digest-addressed OCI repository or
   versioned S3 prefix; keep only references in CRD status.
5. Support `Observed`, `CrashConsistent` and, with a successful browser flush
   hook, `ApplicationConsistent` commits.
6. Support metadata/manifest diff, cheap branches, dry-run checkout and restore
   into a new namespace/profile identity.
7. Emit the six indicator families per component and runtime, with evidence
   timestamps and stable reason codes.
8. Implement conservative mark-and-sweep GC with dry-run as the default and
   `Retain` awareness.

### Explicit exclusions

- no live process or memory restore;
- no multi-Pod atomic checkpoint;
- no direct Ceph cluster mutation;
- no S3 implementation or transparent POSIX emulation;
- no live TCP/IP identity restoration;
- no secret material in manifests;
- no automatic merge of filesystem, browser or memory state.

### MVP acceptance tests

1. Commit a stopped/quiesced terminal workspace and browser profile.
2. Change files and browser state, create a second commit, and show a component
   and manifest diff.
3. Destroy the runtime incarnation, restore the first commit to new PVC/profile
   identities, and validate the marker files and browser profile behavior.
4. Branch from the first commit, advance both branches independently and prove
   branch CAS prevents a lost update.
5. Make CephFS provisioning unavailable and prove restore is `Blocked` rather
   than starting with an empty workspace.
6. Revoke a credential binding and prove restore re-resolution fails without
   exposing the old credential.
7. Mark a commit unreachable, run GC dry-run, pin it during the grace period and
   prove the artifact is not deleted.
8. Restore a sampled commit in a clean target and record a new
   `restorability=Verified` observation.

## 16. Security and policy

- Treat memory checkpoints and browser profiles as high-sensitivity artifacts.
- Encrypt artifacts in transit and at rest; bind access to runtime/tenant and
  operation identity.
- Use separate RBAC verbs or admission policy for commit, artifact read,
  checkout, restore, branch mutation and GC.
- Disable ServiceAccount token automount for adapter jobs unless explicitly
  required; grant narrow resource names and namespaces.
- Sign canonical commit manifests and record adapter image digests.
- Never return raw Secret data, browser cookies or memory contents through CRD
  status, events, logs, metrics or diff output.
- Audit fencing, branch moves, restore execution and GC deletion with operation
  IDs and target digests.

## 17. Reliability and scale

- Reconciliation and adapter calls are idempotent; operation IDs survive
  controller restarts.
- Per-runtime leases serialize barriers and writable restore, while observations
  remain concurrent.
- Commits are immutable and content-addressed, making retries safe after seal.
- Large inventories are chunked in the artifact store using a Merkle-tree-style
  manifest so diff and GC do not overload etcd.
- Observer polling uses backend-aware intervals and jitter. Event-driven updates
  can reduce lag but do not replace periodic verification.
- Backpressure limits concurrent Ceph/CSI snapshots, browser quiesces and restore
  probes by adapter and failure domain.

## 18. Open questions and later phases

Revisit after the MVP:

- whether OCI artifacts, S3 manifests or both become mandatory commit storage;
- application hook protocol and how to attest successful quiesce;
- portable process-checkpoint compatibility matrices across kernel, CRI, OCI
  runtime, CPU and security policy;
- coordinated commits across multiple Pods/databases and bounded consistency
  groups;
- incremental Ceph/S3 manifests and deduplication economics;
- semantic browser-profile migrations across Chromium/Steel versions;
- DRA/GPU/device state and microVM snapshot adapters;
- multi-cluster replication, disaster-recovery branch promotion and commit
  signing trust roots;
- formal SLOs for observation freshness and verified restore age.

## 19. Upstream references

- Kubernetes Volume Snapshots:
  <https://kubernetes.io/docs/concepts/storage/volume-snapshots/>
- Kubernetes Kubelet Checkpoint API:
  <https://kubernetes.io/docs/reference/node/kubelet-checkpoint-api/>
- Kubernetes CRI:
  <https://kubernetes.io/docs/concepts/containers/cri/>
- Kubernetes pod-level checkpoint/restore KEP (experimental design; do not
  assume cluster availability):
  <https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5823-pod-level-checkpoint-restore>
- CephFS snapshots:
  <https://docs.ceph.com/en/latest/cephfs/snapshots/>
