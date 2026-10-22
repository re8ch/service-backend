# Cloud desired-state authority

Status: migration architecture and initial coverage audit  
Date: 2026-10-22  
Scope: node configuration, Kubernetes declarations, charts, configuration,
secret references, release locks, identity and reconciliation evidence

## 1. Decision

GitHub `re8ch/service-backend` will become the complete portable authority for
RE8CH desired state. A cluster or node must be reconstructable from a signed
repository revision plus explicitly enumerated bootstrap roots. Local files,
live Kubernetes objects and controller caches may realize the declaration, but
must not be the only copy of configuration intent.

This includes:

- node identity, roles, labels, taints, addresses, capacity and failure domain;
- K3s/kubelet/API-server/container-runtime configuration and versions;
- network, storage, systemd and host integration declarations;
- every enabled workload, namespace, CRD and dependency edge;
- chart source, chart version, values and rendered release digest;
- ConfigMap source data and mount/env projections;
- Secret name/key contracts plus SOPS ciphertext or external secret reference;
- image repository, immutable digest and supply-chain evidence;
- Git/OCI repository URL, revision/digest and signature policy;
- ServiceAccount, projected token audience/TTL and RBAC;
- CredentialContract, approval, ownership, lifecycle and rollback policy.

The repository does **not** store plaintext credentials, live bearer tokens,
private root keys, etcd contents, PVC data, telemetry streams or Kubernetes
status. Those are runtime data, not desired configuration.

The machine-readable migration ledger is
[`cloud-desired-state-coverage.yaml`](cloud-desired-state-coverage.yaml).

## 2. Target control loop

Flux is one implementation of a reconciliation loop, not the authority itself.
RE8CH can move most of that loop outside the cluster while preserving its
essential semantics.

```text
signed GitHub main
       |
       v
declaration compiler ----------------------+
       |                                    |
       +-> schema/policy/render/diff         |
       +-> immutable OCI release bundle     |
       +-> CredentialContract projections   |
       |                                    |
       v                                    |
GitHub App Observer -- short OIDC/Broker token
       |                                    |
       +---------- read Kubernetes API <----+
       |                    |
       |                    v
       |          observed state + admission
       v
Check / Deployment / deduplicated Issue
       |
       v approved exact revision
GitHub App Executor -- short OIDC/Broker token -> server-side apply/prune
```

The static compiler knows what should exist. The Kubernetes API knows what does
exist. Observer performs the comparison. Executor performs only the approved
transition. Neither requires a standing kubeconfig.

The loop must retain controller-grade behavior:

- `generation`/revision acknowledgement;
- idempotent server-side apply and field ownership;
- dependency ordering and CRD establishment;
- inventory and bounded prune;
- admission/defaulting-aware diff;
- retry with backoff and terminal-versus-transient classification;
- health verification, rollback and deletion/finalization evidence.

Removing Flux Pods does not authorize reducing these semantics to `kubectl
apply` in a workflow.

## 3. Three-state model

RE8CH must never collapse desired, compiled and observed state into one file.

| State | Location | Authority |
| --- | --- | --- |
| Desired | reviewed source YAML/JSON in GitHub | human and policy intent |
| Compiled | generated manifests and immutable OCI digest | deterministic build result |
| Observed | Kubernetes API, provider API and telemetry | current runtime fact |

Generated manifests carry source revision, compiler version, contract digest
and owner annotations. Observed evidence records the same identifiers in a
Check or Deployment. A mismatch is drift; copying a live object back into Git
without classification is not reconciliation.

## 4. Repository model

The target layout is:

```text
contracts/
  clusters/<cluster>/cluster.yaml
  nodes/<node>.yaml
  node-profiles/<profile>.yaml
  workloads/<domain>/<workload>.yaml
  releases/<domain>/<release>.yaml
  configuration/<domain>/<config>.yaml
  secrets/<domain>/<secret-contract>.yaml
  credentials/<subject>.yaml
  targets/{github,kubernetes,brokers,projects}.yaml
locks/
  charts.lock.yaml
  images.lock.yaml
  sources.lock.yaml
generated/
  clusters/<cluster>/
  bundles/<domain>/
  credentials/{github,kubernetes,broker,observability,governance}/
evidence/
  schemas/
  policy/
  provenance/
```

Existing authoritative paths remain in place until their declarations are
migrated and parity-tested. This layout is a target, not permission to duplicate
an authority indefinitely.

### 4.1 NodeContract

One `NodeContract` replaces the current split between node inventory, AI target
inventory, host files and node labels:

```yaml
apiVersion: infrastructure.re8ch.com/v1alpha1
kind: NodeContract
metadata:
  name: r640
  uid: node_01J...
  owner: platform-infrastructure
spec:
  clusterRef: re8ch
  lifecycle: active
  provider: onprem
  region: cn-hunan
  failureDomain: r640-idc
  roles: [worker, pg-re8ch, public-gateway-backend]
  network:
    internalIP: 10.181.22.128
    acceleratedIP: 10.250.0.128
    podCIDR: 10.42.6.0/24
    publicAuthorityRef: operations/network/manifests/network/public-mesh/inventory.json
  kubernetes:
    distribution: k3s
    role: agent
    versionRef: locks/kubernetes.lock.yaml#k3s
    profileRef: control-large
    labels: {}
    taints: []
  host:
    osProfileRef: ubuntu-re8ch-v1
    systemdProfiles: []
    storageProfiles: [pg-re8ch]
  bootstrap:
    joinCredentialRef: broker://cluster/re8ch/node/r640
    trustBundleRef: public://cluster/re8ch/bootstrap-roots
  observation:
    requiredFacts: [nodeReady, kubeletVersion, kernelVersion, capacity,
                    addresses, labels, taints]
```

The contract references the existing R640 public network authority. It never
copies or overrides that authority.

### 4.2 ReleaseContract

Every running chart or raw manifest receives one immutable release declaration:

```yaml
apiVersion: delivery.re8ch.com/v1alpha1
kind: ReleaseContract
metadata:
  name: kube-state-metrics
spec:
  target: kubernetes/re8ch/observability
  source:
    type: helm
    repository: https://prometheus-community.github.io/helm-charts
    chart: kube-state-metrics
    version: 8.3.0
    digest: sha256:...
  valuesRef: contracts/releases/observability/kube-state-metrics.values.yaml
  renderedDigest: sha256:...
  images:
    - repository: registry.k8s.io/kube-state-metrics/kube-state-metrics
      digest: sha256:...
  dependencies: []
  lifecycle:
    strategy: serverSideApply
    prune: true
    healthTimeout: 5m
    rollbackRef: release://kube-state-metrics/previous
```

Tags and chart versions remain useful labels, but production execution uses
digests.

### 4.3 Configuration and Secret contracts

ConfigMap source data belongs in Git when it is non-secret and reviewable.
Runtime-specific fields are generated from a typed configuration contract.

A Secret contract records names and keys without exposing values:

```yaml
apiVersion: identity.re8ch.com/v1alpha1
kind: SecretContract
metadata:
  name: example-runtime
spec:
  target: kubernetes/re8ch/example
  keys:
    - name: API_TOKEN
      source: sops://clusters/re8ch/secrets/example-runtime.sops.yaml#API_TOKEN
      rotation: 30d
      consumers: [deployment/example]
  plaintextForbidden: true
```

Acceptable value backends are SOPS ciphertext committed to Git, an external
secret manager reference, or a Broker-derived ephemeral credential. A live-only
Kubernetes Secret is migration debt unless explicitly classified as bootstrap
or runtime-generated material.

## 5. Projected identity, not projected static secrets

The repository declares a projected ServiceAccount token volume, its audience,
expiration and consumer. Kubernetes TokenRequest creates the actual token at
runtime and kubelet rotates it. The token itself never enters GitHub.

```yaml
volumes:
  - name: workload-identity
    projected:
      sources:
        - serviceAccountToken:
            path: token
            audience: https://broker.re8ch.com
            expirationSeconds: 300
```

The corresponding CredentialContract compiles:

- ServiceAccount and `automountServiceAccountToken: false`;
- exact Role/RoleBinding;
- token audience and maximum TTL;
- Broker exchange rule;
- lifecycle observation and revocation test;
- GitHub governance projection.

This is the mechanism that lets GitHub hold the complete identity declaration
without holding a reusable Kubernetes credential.

## 6. Initial repository coverage audit

The initial audit shows that the repository is already a substantial desired-
state store, but not yet a complete reconstruction source:

- 545 YAML files exist below `clusters/re8ch`;
- declarations include 75 Flux Kustomizations, 21 HelmReleases, 32
  OCIRepositories, 10 HelmRepositories and six GitRepositories;
- 89 files contain ConfigMaps and 67 contain Secrets;
- 54 SOPS-encrypted files are present;
- 41 files use hostPath and therefore depend on host state outside Kubernetes;
- 115 files contain at least one image reference without an immutable digest;
- six files contain a `:latest` image reference;
- the cluster inventory lists 14 nodes while the AI operations inventory lists
  13, with six names present only in the former and five only in the latter;
- K3s server declarations cover A1 and a B1 DR template; the checked-in R640
  server declaration is explicitly retired and many worker/node host settings
  are represented only indirectly;
- the available `r640-k3s-ssh` API path returned HTTP 503 for `get nodes` during
  this audit, so live node/config parity is unverified.

These counts measure files containing a kind or reference, not unique live
objects. They are migration evidence, not an assertion that all declarations
are active.

## 7. What cannot be moved verbatim

Only a small bootstrap set remains outside Git, and every item must have a
public descriptor, owner, recovery process and rotation evidence in Git:

1. node firmware, bootloader and initial OS image;
2. first network path required to reach the Kubernetes API, GitHub or Broker;
3. cluster CA private key and Broker signing root;
4. node join credential or one-time enrollment proof;
5. SOPS/KMS private decryption material;
6. etcd database, PVC contents and application data;
7. live short-lived tokens produced by TokenRequest or Broker;
8. emergency break-glass credential held outside the normal trust chain.

“Outside Git” does not mean “undocumented on a laptop.” Git stores the public
key/fingerprint, location URI, owner, rotation deadline, restore test and
revocation procedure. Only secret material and runtime data remain external.

## 8. Migration sequence

### Wave 1 — inventory convergence

- create canonical ClusterContract, NodeContract and target inventory schemas;
- merge the two current node inventories by stable node UID;
- classify retired, controlled-subcluster and external nodes explicitly;
- recover live parity only after the API observation path is healthy;
- do not rewrite public network authority while normalizing nodes.

### Wave 2 — host declaration

- import K3s agent/server configuration for every active node;
- declare systemd units, kernel/sysctl, mounts, storage and host packages;
- replace imperative host updater Jobs with compiled NodeContract actions where
  safe;
- record hostPath producers, ownership and reconstruction tests.

### Wave 3 — release locks

- convert every HelmRelease, local chart and raw workload to ReleaseContract;
- resolve chart archives and container images to immutable digests;
- eliminate `latest` from active declarations;
- publish deterministic rendered bundles to OCI.

### Wave 4 — configuration and secrets

- make every ConfigMap source traceable to a typed contract;
- inventory every live Secret name/key without reading values into logs;
- adopt SOPS, external secret references or Broker credentials;
- reject undocumented live-only configuration.

### Wave 5 — external reconciliation

- activate read-only Observer contracts against the public Kubernetes API;
- compare compiled inventory with server-side dry-run and live ownership;
- activate Executor one namespace and one reversible capability at a time;
- retire a Flux responsibility only after the external loop proves equivalent
  apply, inventory, prune, health and rollback behavior.

## 9. Admission and merge rules

The compiler and GitHub ruleset reject:

- unpinned active images or charts after their migration deadline;
- local filesystem paths without a NodeContract producer;
- ConfigMaps or Secret references without an owner and source contract;
- Kubernetes credentials, join tokens or plaintext secrets;
- a resource claimed by more than one pruning owner;
- a node identity present in multiple inventories with conflicting values;
- generated output that does not match the source declarations;
- runtime mutation without a CredentialContract and exact target capability;
- deletion while observed inventory, data retention or finalizers are unresolved.

## 10. Completion criteria

The migration is complete when:

- a clean machine can compile every cluster and node from one signed revision;
- every active node maps to exactly one NodeContract and observed node UID;
- every running workload maps to one ReleaseContract and immutable source/image
  digest;
- every ConfigMap and Secret key has a declared source and owner;
- every hostPath has a declared host producer and recovery test;
- the only manual inputs are the enumerated bootstrap roots;
- external Observer can account for all managed live objects;
- external Executor can reproduce Flux-equivalent convergence for migrated
  scopes without a standing kubeconfig;
- repository deletion/restoration exercises reconstruct the declaration store
  from GitHub export and immutable OCI artifacts.

