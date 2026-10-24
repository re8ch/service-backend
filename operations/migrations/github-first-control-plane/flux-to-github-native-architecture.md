# Flux-to-GitHub-native control-plane architecture

Status: architecture proposal; no implementation authorized  
Date: 2026-10-22  
Scope: `service-backend`, its federated GitOps repositories, and GitHub Enterprise collaboration/security capabilities

## 1. Executive decision

RE8CH should **not replace Flux with GitHub**. Nor should it replace every
validation with a build pipeline. It should shrink Flux to the part
that cannot be static: continuously observing Kubernetes and converging runtime
state. GitHub should become the authority for declarations, review, static
compilation, policy admission, security analysis, work tracking, evidence and
release intent. A small, bounded execution plane should perform only the final
runtime read/write operations that require network reachability and Kubernetes
credentials.

This is a **validation-driven, bidirectional GitOps** design:

- Repository = Desired State.
- Kubernetes = Actual State.
- Issues/Projects = Engineering State: facts worth investigating, decisions,
  owners and lifecycle—not a telemetry database.
- Flux/executor = deterministic convergence mechanism, not the decision-maker.
- AI = evidence-grounded investigator and proposer, not an authority.

The target split is:

```text
                         GitHub Enterprise
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Repository declarations + schemas + immutable locks                │
  │ PR/ruleset admission + CODEOWNERS + merge queue                    │
  │ GitHub-hosted Actions: render, validate, test, sign, attest         │
  │ Code Quality / CodeQL / Dependabot / secret scanning               │
  │ Issues: durable actionable events; Projects: derived portfolio view│
  │ AI: advisory diagnosis and proposed patches, never authority       │
  └───────────────────────────────┬─────────────────────────────────────┘
                                  │ signed commit / digest / dispatch
                                  v
                     Minimal execution boundary
              ┌─────────────────────────────────────┐
              │ GitHub OIDC -> short-lived identity │
              │ webhook verification + queue/dedupe │
              │ cluster probe/apply/rollback adapter│
              └──────────────────┬──────────────────┘
                                 │ bounded API verbs
                                 v
                          Kubernetes runtime
              ┌─────────────────────────────────────┐
              │ Flux source + apply controllers     │
              │ Kubernetes API/status/events        │
              │ health, inventory, drift, GC        │
              └──────────────────┬──────────────────┘
                                 v
                   Prometheus / Loki / traces / Headlamp
```

The main cost reduction comes from moving deterministic compilation and
analysis off cluster and onto GitHub-hosted runners, eliminating duplicate
checks, and reducing controller scope. GitHub Code Quality is not compute-free:
it runs CodeQL using GitHub Actions and consumes Actions minutes. It removes the
need to operate that compute in the cluster only when its jobs are kept on
GitHub-hosted runners.

## 2. Constraints and definitions

### 2.1 Authorities

| Concern | Authority | Reason |
| --- | --- | --- |
| Desired configuration | signed `main` commit and immutable artifact digest | reviewable, portable history |
| Admission decision | GitHub ruleset + required checks + CODEOWNERS | merge is the state transition |
| Runtime object state | Kubernetes API | only the API server knows observed state |
| Runtime convergence | Flux or an explicitly named product publisher | requires live watch, retries and ownership |
| Raw telemetry | Prometheus/Loki/tracing systems | volume and query model do not fit Git |
| Actionable incident/work | GitHub Issue | durable, assigned, reviewable projection |
| Portfolio view | GitHub Project | derived view, not an authority |
| Secrets | external secret system / encrypted SOPS payload | plaintext must not enter GitHub content or logs |
| AI output | proposal, finding or draft PR | probabilistic output cannot become desired state directly |

“Static” means a result can be computed from a pinned repository tree and
immutable external inputs without reading a live cluster. “Dynamic” means the
answer depends on current time, remote availability, Kubernetes discovery,
admission/defaulting, workload health, or another changing runtime state.

### 2.2 Current RE8CH evidence

The checked-in `clusters/re8ch/flux-system/gotk-components.yaml` declares the
four default Flux controllers and these CRDs:

- Source: `Bucket`, `GitRepository`, `HelmChart`, `HelmRepository`,
  `OCIRepository`.
- Apply: `Kustomization` and `HelmRelease`.
- Notification: `Alert`, `Provider`, and `Receiver`.

No `ImageRepository`, `ImagePolicy`, `ImageUpdateAutomation`,
`ExternalArtifact`, or `ArtifactGenerator` CRD is present in that checked-in
main-stack manifest. The image controllers and source-watcher are therefore
upstream capabilities, not established current RE8CH runtime dependencies.

The repository already has four autonomous controller stacks (`agent`,
`identity`, `observability`, `network`), five independently reconciled business
domains, and a deliberate product-publisher boundary for high-cadence
applications. This proposal preserves those ownership boundaries.

At this revision, every declared workflow job targets a self-hosted runner.
Portable Linux work uses the eight-instance R640 `untrusted-build` pool; host-
bound and Buildx work uses the Macmini, with `trusted-audit` reserved for the
two host inspection workflows. CodeQL is disabled. Runner capacity, queue time,
registry egress, and controller resource usage retain separate baselines.

### 2.3 Prior decisions and operational evidence

This proposal is a consolidation of prior work, not a greenfield redesign. The
following repository history is treated as architectural evidence:

| Evidence | Established decision or observed fact | Consequence here |
| --- | --- | --- |
| PR #1-era commit `b14f3bf2a`, “establish GitHub-first coordination” | GitHub is collaboration authority; Git is durable transition log; Kubernetes remains runtime authority; raw telemetry stays outside Git | retain the existing authority grammar rather than inventing a second control plane |
| PR #68, “retire GitLab control plane” | validation/release reactions moved to GitHub Actions; GitHub became generated control-plane authority; R640 GitLab runner state was removed | new static work defaults to GitHub-hosted Actions, not another cluster CI stack |
| PR #1922, “Define product runtime authority boundary” | external high-cadence products move to authenticated publishers; Flux retains platform guardrails | do not force all application delivery back through a central Flux owner |
| PR #1926 and #1931, Flux federation/autonomy | domain controller stacks and repositories were intentionally isolated; ownership transfer uses freeze, canary, then prune | preserve domain autonomy and single-writer migration phases |
| PRs #1988, #1992, #1993, #1996, #1997, #2002, #2006, #2009–#2011 | real reconciliation repeatedly required narrowly expanded RBAC for Helm storage, CRD adoption, leases and exact resource kinds | offline policy can reduce errors but cannot discover or safely grant runtime authority automatically |
| PRs #2080–#2084 | root source reconciliation repeatedly failed on GitHub reachability and proxy protocol/topology | moving compilation to GitHub reduces cluster source traffic, but runtime source acquisition still needs an outage model/cache |
| PRs #2092–#2095 | controller recovery depended on API reachability, node placement, taints, rollout strategy and concurrency | controller scheduling and control-plane reachability are first-class runtime dependencies, not repository-only concerns |
| PR #2058 and PR #1892 | Helm releases can stall, roll back, exhaust retries or fail on namespace-scoped runtime dependencies | retain Helm lifecycle where used; a green render is not release success |
| PRs #1833 and #1839 | server-side dry-run/admission exposed a Kyverno/Secret RBAC cycle; resolution required execution-namespace isolation and ordering | live admission validation and runtime dependency ordering remain mandatory |
| PRs #1869–#1875 | a workflow, Check and Artifact were insufficient until a real nonzero runtime test passed | deployment acceptance is observed behavior, not pipeline completion |

The relevant prior design conversations add two binding principles:

1. Prefer **Validation-Driven Delivery**: continuous static, contract,
   infrastructure and runtime verification; build only when an artifact must be
   produced or changed.
2. Compile runtime facts into a deduplicated Issue/Project lifecycle:
   `Detected -> Investigating -> Proposed -> Reviewing -> Approved -> Executing
   -> Verifying -> Resolved`. Short self-healing noise does not become an Issue.

Consequently, “reduce cluster load” means removing duplicate builds, scans and
source packaging from the cluster. It does not mean removing the observers and
reconcilers that prove the real system state.

## 3. Flux component inventory and decomposition

### 3.1 Common controller reconciliation contract

All Flux controllers follow the Kubernetes controller pattern:

1. Watch a primary CR and selected dependent resources.
2. Enqueue a namespaced object key on generation, dependency, source artifact,
   timer, manual annotation, or external receiver event.
3. Read desired `spec`, dependent status and credentials.
4. Resolve/fetch/build/apply the requested state.
5. Persist observed state in `.status`, including `observedGeneration`,
   Conditions, revisions/digests and controller-specific inventory/history.
6. Emit Kubernetes Events and metrics.
7. Requeue at `spec.interval`, on a retry/backoff, or on a new watch event.
8. Execute finalizer cleanup when the CR is deleted.

This loop is level-based and idempotent, not a one-shot pipeline. A GitHub
workflow can reproduce steps 3-4 for a fixed input and report a check, but it
cannot reproduce steps 1, 5, 7 and 8 continuously without becoming another
controller and state store.

Common status concepts that must not be flattened into a single CI result:

- `metadata.generation` versus `status.observedGeneration` proves whether the
  controller has processed the latest desired generation.
- `Ready`, `Reconciling` and `Stalled` Conditions describe current controller
  progress and terminal/transient failure.
- source artifact revision/digest identifies controller input;
  `lastAppliedRevision` or release history identifies runtime effect.
- Kubernetes Events are ephemeral diagnostic facts; they are not durable audit
  records.
- inventory and finalizers encode ownership and safe garbage collection.

### 3.2 Source controller

**Responsibility.** Acquire Git, OCI, Helm and bucket content; authenticate and
verify it; select revisions; package artifacts; expose artifacts to downstream
controllers; report source readiness.

| API | Desired input | Produced state/artifact | Main dependencies |
| --- | --- | --- | --- |
| `GitRepository` | URL, ref, include rules, ignore rules, verification, interval | tar artifact with commit revision/digest | Git server, auth Secret, optional signature keys |
| `OCIRepository` | OCI URL, ref/semver, layer selector, verification | pulled/extracted artifact and digest | OCI registry, auth Secret, cosign/notation trust |
| `Bucket` | endpoint, bucket, region, prefix, provider, interval | archived object set and digest | S3-compatible/provider API, credentials |
| `HelmRepository` | HTTP/OCI index endpoint and interval | HTTP repository index artifact; OCI form is metadata-only | Helm endpoint/registry and credentials |
| `HelmChart` | chart/version/values files and `sourceRef` | selected or packaged chart archive | ready source artifact |

Newer upstream source APIs also include `ExternalArtifact` and the
source-watcher-owned `ArtifactGenerator`; they are not in the current RE8CH
main manifest. `ArtifactGenerator` composes/decomposes source artifacts and can
give monorepo paths independent lifecycles.

**Events and triggers.** Poll interval; CR/Secret/ConfigMap changes; source
revision change; manual `reconcile.fluxcd.io/requestedAt`; notification
Receiver event; dependency artifact change.

**State management.** Artifact content is stored by the controller and exposed
over its internal HTTP service. Status records revision, digest, URL, size,
timestamps and Conditions. Local artifact/cache state is reconstructible but
is required for downstream runtime operation.

**GitHub mapping.** A repository commit, release, tag or GHCR digest is the best
static source record. Actions can resolve semver, vendor dependencies, verify
signatures, build a deterministic bundle and publish it to GHCR with provenance
and attestations. Dependabot can propose dependency changes. This statically
replaces source *selection and packaging* for reviewed releases; it does not
replace private non-GitHub fetches, live polling, in-cluster artifact serving,
credential rotation detection or source Ready status.

**Recommended disposition.** Precompile domain bundles in GitHub Actions and
pin immutable OCI digests. Retain a minimal source-controller for Git/OCI
acquisition until the executor consumes immutable bundles directly. Avoid
dynamic semver ranges in production desired state.

### 3.3 Kustomize controller

**Responsibility.** Build Kustomize overlays, decrypt SOPS material, validate
against Kubernetes, apply objects, wait for health, order dependencies and
prune objects from its inventory.

**CRD.** `Kustomization` (`kustomize.toolkit.fluxcd.io/v1`). Key intent includes
`sourceRef`, `path`, `interval`, `timeout`, `dependsOn`, `healthChecks`,
`wait`, `prune`, `force`, `retryInterval`, `serviceAccountName`,
decryption, patches, substitutions and suspension.

**Dependencies.** Ready source artifact; Kubernetes discovery and admission;
RBAC/impersonated service account; optional SOPS/KMS keys; dependent
Kustomizations and health targets.

**Events and triggers.** Spec/generation changes, source artifact revision,
dependency readiness, interval/retry timer, manual reconcile, referenced
ConfigMap/Secret changes.

**State management.** Conditions, observed generation, attempted/applied
revision, object inventory and health results. Inventory plus field ownership
enables prune. Finalization can delete owned resources.

**Reconciliation.** Fetch artifact, securely build/decrypt, validate, perform
server-side apply, record inventory, assess health, then prune stale inventory
when allowed. Failed health or dependency gates causes retry and may leave a
partially applied but observable state.

**GitHub mapping.** Actions can run pinned Kustomize, schema validation,
policy-as-code, decryption-free structural checks, manifest diff and artifact
publication before merge. Rulesets and environments can enforce approval.
They cannot statically reproduce API discovery, admission webhooks, mutation,
server-side apply conflicts, real health, live inventory, finalizers or prune.

**Recommended disposition.** Move build and policy validation to GitHub-hosted
Actions; publish exact bundles. Keep only apply/inventory/health/prune in a
namespace-bounded controller or minimal executor. Continue the existing rule
that one object has at most one active `prune: true` owner.

### 3.4 Helm controller

**Responsibility.** Declaratively manage Helm install, upgrade, test, rollback,
uninstall and remediation.

**CRD.** `HelmRelease` (`helm.toolkit.fluxcd.io/v2`). The controller commonly
creates a `HelmChart` source object from `spec.chart`; it may also use a direct
chart reference, values references, dependency ordering, post-renderers,
install/upgrade/test remediation and suspension.

**Dependencies.** source-controller and a ready chart artifact; Helm storage
Secrets; Kubernetes discovery/admission/RBAC; values Secrets/ConfigMaps;
dependent releases; hook/test Jobs and workload health.

**Events and triggers.** HelmRelease generation, chart artifact revision,
referenced values change, dependency transition, interval/retry, manual request.

**State management.** Conditions, observed generation, attempted revision,
release revision/history and failure counters. Helm's release Secrets are
runtime transaction state and are not represented by Git history.

**Reconciliation.** Resolve chart/values, compare desired and installed
release, run Helm action, execute hooks/tests, assess success, then follow
configured retry/rollback/uninstall remediation. Deletion invokes uninstall
unless policy says otherwise.

**GitHub mapping.** Actions can pin/fetch the chart, render templates, lint,
test schemas, scan images, diff normalized manifests, sign the bundle and gate
promotion. Releases and GHCR can hold immutable charts. Dependabot can propose
chart version bumps when the dependency is declared in a supported manifest or
through a custom update workflow.

**Not statically replaceable.** Helm ownership/history, hooks, API defaulting,
CRD establishment order, rollback after runtime failure, test Jobs, uninstall
and live remediation.

**Recommended disposition.** Prefer pre-rendered, digest-pinned bundles for
simple charts. Retain Helm controller only for releases that genuinely require
Helm lifecycle semantics. Do not emulate Helm transactions with a generic
`kubectl apply` Action.

### 3.5 Notification controller

**Responsibility.** Receive signed external events and trigger source
reconciliation; filter Flux Kubernetes Events and deliver outbound
notifications.

| CRD | Role | Durable state? |
| --- | --- | --- |
| `Receiver` | webhook endpoint, provider type, secret, target refs | configuration only; dedupe must exist elsewhere |
| `Alert` | event severity and object filters | routing declaration |
| `Provider` | Slack/Teams/webhook/Git provider destination and credentials | routing declaration |

**Dependencies.** Kubernetes Events, receiver Service/Ingress/Gateway,
provider APIs, authentication Secrets, DNS/network and target Flux objects.

**Events and triggers.** External webhooks on inbound Receivers; Kubernetes
Events matching Alerts on outbound; controller/config changes.

**State management.** CR Conditions and Kubernetes Events; delivery retry and
provider responses are operational state, not a complete durable queue/audit
ledger.

**GitHub mapping.** Native GitHub events trigger Actions, Checks, Issues,
deployments and Project automation without notification-controller. Flux events
can be projected into GitHub as deployment statuses or deduplicated Issues.
GitHub cannot receive non-GitHub sources or observe cluster events without an
external ingress adapter. Issues are suitable only for actionable, redacted,
low-cardinality events—not raw event streams.

**Recommended disposition.** Replace GitHub-origin inbound Receivers with
native GitHub workflow events. Replace human-facing outbound messages with a
single event gateway that normalizes/deduplicates and writes deployment/check
status or Issues. Keep notification-controller only where it provides a needed
cluster-local fan-out during migration.

### 3.6 Image reflector controller

**Responsibility.** Scan container registries and reflect available tags and
metadata into Kubernetes API state.

**CRDs.** `ImageRepository` defines registry scan/auth/exclusion; `ImagePolicy`
selects a tag by semver, alphabetical or numerical policy and may extract a
value before comparison.

**Dependencies.** registry, credentials, local metadata database/cache, policy
objects. Events are scan intervals, spec/Secret changes and manual reconcile.
Status exposes scan result, selected image and Conditions.

**GitHub mapping.** GHCR package events, immutable digests, releases and
Dependabot-style PRs can replace polling for images produced in GitHub. A
scheduled GitHub-hosted Action can inspect third-party registries and open a
version PR. Static declarations should pin digest, never deploy a mutable tag.

**Gap.** GitHub has no universal native registry-policy controller for every
external registry, and a schedule remains an executor. Registry availability,
tag mutation and credentials are dynamic.

**Recommended disposition.** Do not add this controller to RE8CH solely for
updates. Use producer-triggered digest PRs for first-party images and a small,
GitHub-hosted scheduled updater for external images.

### 3.7 Image automation controller

**Responsibility.** Read selected `ImagePolicy` results, edit marked YAML files,
commit and push changes to Git.

**CRD.** `ImageUpdateAutomation` declares source checkout, update strategy,
path, Git commit identity/message and push target.

**Dependencies.** source-controller Git artifact, image-reflector APIs, writable
Git credentials and branch state. Events are source/policy changes, interval and
manual reconcile. Status records observed source revision, last automation run,
last push commit/time and Conditions.

**GitHub mapping.** This is strongly replaceable with a GitHub App/Action that
opens a PR containing a digest update. Rulesets, CODEOWNERS, checks and merge
queue provide safer separation than direct controller pushes. Dependabot is the
preferred native engine where it supports the ecosystem.

**Recommended disposition.** Keep absent. Never grant a cluster controller
routine write access to protected GitHub branches. Use reviewable bot PRs.

### 3.8 Source-watcher and ArtifactGenerator

**Responsibility.** Compose several source artifacts or split a monorepo into
independently changing artifacts, with path-sensitive lifecycles.

**CRD.** `ArtifactGenerator`; source-controller documentation also lists
`ExternalArtifact`. These newer APIs are optional and absent from the current
RE8CH manifest.

**GitHub mapping.** Paths filters, changed-file impact analysis, reusable
workflows, matrix jobs and independently published OCI bundles replace most
build-time use cases. Cross-repository composition should consume immutable
digests or commit SHAs, never floating branches.

**Gap.** GitHub workflow completion is not a continuously served in-cluster
artifact dependency graph. Use GHCR plus an explicit lockfile as the durable
graph and retain a source fetcher at runtime.

### 3.9 Flux CLI, bootstrap and shared runtime facilities

The Flux CLI is an operator/client, not a controller. `flux bootstrap` installs
CRDs, RBAC, network policies and controllers and writes sync declarations.
`flux reconcile`, `suspend`, `resume`, `trace`, `tree`, `events`, `logs` and
status commands are imperative operational clients. They cannot be mapped to
repository data alone.

Shared facilities include leader election, controller-runtime caches/watches,
work queues, metrics, health/readiness endpoints, Kubernetes Events, service
accounts/RBAC, network policies, webhooks/services and artifact storage. GitHub
can store their desired configuration and test it, but Kubernetes must run them.

## 4. Capability mapping

Legend: **S** fully static/native; **H** hybrid; **D** dynamic and requires a
runtime component; **N/A** different concern rather than a replacement.

| Flux capability | GitHub Repository/native target | Class | Replacement decision |
| --- | --- | --- | --- |
| Desired CR specs | versioned YAML/JSON + JSON Schema | S | canonical declaration |
| Source commit selection | protected branch, tag, release, SHA lock | S | use immutable SHA/digest |
| Source artifact build | GitHub-hosted Actions + GHCR | S/H | deterministic build; remote fetch still executes |
| Signature verification | rulesets, vigilant mode, Actions verification, attestations | H | verify both commit and artifact |
| Kustomize build | pinned GitHub-hosted Action/job | S | remove from cluster hot path where possible |
| Helm render/lint | pinned GitHub-hosted Action/job | S | keep runtime Helm only when lifecycle needed |
| Schema/policy checks | required Checks, reusable workflows | S | pre-merge admission |
| Source polling | `schedule`, repository/package events | H | scheduled workflow still consumes compute; events preferred |
| Dependency updates | Dependabot PRs | S/H | limited by supported package ecosystems |
| Image digest updates | Dependabot/custom GitHub App PR | H | producer event preferred over polling |
| Code maintainability | Code Quality | S/H | CodeQL + AI findings; not manifest/runtime validation |
| Vulnerability analysis | CodeQL/code scanning | S/H | source-level security, not cluster posture |
| Dependency risk | dependency graph/review, Dependabot alerts | S | gate vulnerable dependency changes |
| Secrets in Git | secret scanning + push protection | S | complements, not replaces secret manager/SOPS |
| Ownership and approval | CODEOWNERS + rulesets + merge queue | S | desired-state admission |
| Release promotion | PR + Environment approval + deployment record | H | execution uses Actions |
| Apply/server-side ownership | Kubernetes executor/Flux | D | cannot staticize |
| Admission/defaulting | Kubernetes API dry-run/live admission | D | only cluster can answer accurately |
| Health assessment | Kubernetes + probes/telemetry | D | project result to deployment/check/Issue |
| Drift detection | controller watch or bounded scheduled audit | D | Git diff alone cannot detect live drift |
| Prune/garbage collection | Flux inventory/finalizer | D | retain bounded controller |
| Helm rollback/remediation | Helm controller/runtime executor | D | Git revert is intent, not runtime rollback proof |
| Event routing | webhooks + gateway + Issues/deployments | H | external adapter required for cluster events |
| Runtime status | Deployments/Checks + Issue projection | H | projection only; Kubernetes remains authority |
| Dependency ordering | artifact DAG + workflow `needs` pre-merge; Flux `dependsOn` runtime | H | keep runtime readiness edges |
| Suspend/resume | reviewed declaration; emergency platform tool | H/D | change intent static; immediate brake dynamic |
| Multi-tenancy | repos/teams/environments + K8s RBAC/service accounts | H | both planes required |

## 5. Target repository structure

The proposal extends, rather than replaces, the existing repository grammar.

```text
service-backend/
├── .github/
│   ├── CODEOWNERS
│   ├── dependabot.yml
│   ├── ISSUE_TEMPLATE/
│   │   ├── reconcile-failure.yml
│   │   ├── drift.yml
│   │   ├── dependency-update.yml
│   │   ├── security-finding.yml
│   │   └── runtime-incident.yml
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/                 # thin callers; all actions SHA-pinned
│       ├── desired-state-ci.yml
│       ├── bundle-publish.yml
│       ├── release-promote.yml
│       ├── runtime-verify.yml
│       ├── drift-audit.yml
│       └── event-projector.yml
├── schemas/
│   ├── desired-state/
│   ├── release-lock/
│   ├── authority/
│   ├── event-envelope/
│   └── reconciliation-evidence/
├── policy/
│   ├── ownership/
│   ├── admission/
│   ├── risk/
│   └── exceptions/                # owner, reason and expiry required
├── sources/
│   ├── catalog.yaml               # logical source IDs; no credentials
│   └── locks/                     # exact commit/chart/image/bundle digests
├── bundles/
│   ├── definitions/               # build recipes and declared inputs
│   └── indexes/                   # published digest metadata, not large blobs
├── deploy/
│   ├── bootstrap/                 # minimal controller/RBAC installation
│   ├── domains/<domain>/
│   │   ├── desired/
│   │   ├── release-lock.yaml
│   │   ├── ownership.yaml
│   │   └── health-contract.yaml
│   └── environments/<environment>/
│       ├── domains.yaml
│       └── promotion-lock.yaml
├── operations/
│   ├── reconcilers/               # executor declarations and boundaries
│   ├── events/                    # mapping/routing declarations
│   ├── audits/                    # audit definitions; output stored externally
│   └── runbooks/
├── docs/
│   ├── adr/
│   ├── architecture/
│   └── runbooks/
└── tests/
    ├── fixtures/
    ├── conformance/
    └── policy/
```

Large rendered bundles belong in GHCR, not Git. Git stores the build recipe,
input locks, output digest and provenance reference. Existing catalog gitlinks
remain product-source references; this repository must not absorb product code.

Organization-wide reusable workflows, ruleset declarations, repository custom
properties, Issue forms and label taxonomy belong in the organization `.github`
repository. This repository keeps only thin callers and local policy.

## 6. GitHub-native functional design

### 6.1 Pull request admission

Every desired-state PR should produce one immutable evidence set:

1. Validate schemas, authority ledger and single-writer/prune invariants.
2. Resolve every source to a commit or digest; reject mutable production refs.
3. Build Kustomize/Helm output with pinned tools.
4. Scan output for forbidden kinds, namespace escape, RBAC escalation,
   unencrypted Secrets and public-network-authority violations.
5. Run offline Kubernetes schema checks and policy tests.
6. Produce a normalized diff by domain and resource owner.
7. Build/publish a candidate OCI bundle and generate provenance/SBOM where
   applicable.
8. Attach a check summary and artifact digest to the PR.

Rulesets require signed commits, CODEOWNER approval, successful deterministic
checks, dependency review, applicable code-scanning merge protection, merge
queue and no force push. Environment approval is reserved for actual promotion,
not basic CI.

### 6.2 Actions execution classes

| Class | Runner | Allowed access | Examples |
| --- | --- | --- | --- |
| Pure static | GitHub-hosted | repository read, checks write | schema, render, policy, unit tests |
| Artifact producer | GitHub-hosted | contents read, packages/attestations write via scoped token | bundle/chart/image publishing |
| Security/quality | GitHub-hosted default where supported | CodeQL/security results | Code Quality, CodeQL, dependency review |
| Runtime observer | minimal host-bound runner or platform API | Kubernetes read-only, no Secrets read | dry-run, discovery, health and drift evidence |
| Runtime mutator | protected environment + OIDC | allowlisted namespace/resource verbs | emergency brake or explicit publisher |

No general CI job should receive a kubeconfig. Self-hosted runners must not be
selected merely to save GitHub minutes; that would preserve cluster burden and
increase trust exposure. Host-bound runners are reserved for `/Volumes/A1`,
R640 networking, private endpoints and exact architecture/hardware tests.

### 6.3 Projects and Issues

Issues model actionable state, not controller objects. Recommended Issue
dedupe key is `(cluster, controller, kind, namespace, name, reason)` plus a
bounded time window. Repeated Events update occurrence count and last-seen time.

The bridge is an **Event-to-Issue compiler**, not a webhook copier:

```text
Flux Events ───────────┐
Kubernetes Events ─────┤
Prometheus alerts ─────┼─> normalize/redact -> correlate -> stability gate
external heartbeats ───┘                              │
                                                     v
                              actionable fact -> Issue -> Project item
                              transient/noise  -> telemetry only
```

Correlation must distinguish an observed fact, a likely causal relationship
and an AI hypothesis. Temporal adjacency alone is not proof of causality. A
node failure, its evicted Pods and degraded Services normally update one
incident graph rather than opening one Issue per Event.

Project fields are derived: `Status`, `Priority`, `Environment`, `Domain`,
`Controller`, `Resource`, `Risk`, `Owner`, `First seen`, `Last seen`, `Change
ID`, `Deployment`, `Resolution`. Project automation may change presentation;
any authoritative edit must write back to the Issue contract or be reverted.

The default lifecycle is `Detected`, `Investigating`, `Proposed`, `Reviewing`,
`Approved`, `Executing`, `Verifying`, `Resolved`. Moving to `Approved` records
an authorization fact; it does not itself mutate the cluster. `Executing` must
identify the exact PR/commit/digest and executor operation. `Verifying` must
carry fresh runtime evidence.

Auto-close requires positive runtime evidence at the intended revision for a
stability window. A successful workflow alone must never close a runtime
reconciliation incident.

### 6.4 Security, Dependabot, CodeQL and Code Quality

| GitHub feature | Use | Explicit non-goal |
| --- | --- | --- |
| Code Quality | reliability/maintainability/style findings and suggested fixes | Kubernetes drift, YAML policy, Helm health |
| CodeQL code scanning | source security dataflow and merge protection | image/cluster runtime vulnerability proof |
| Dependabot alerts/security updates | known vulnerable dependencies | complete chart/image/source promotion engine |
| Dependabot version updates | routine supported ecosystem PRs | arbitrary OCI tag policy |
| Dependency review | block newly introduced vulnerable dependencies | prove deployed runtime inventory |
| Secret scanning/push protection | prevent supported/custom secret patterns entering Git | manage or decrypt runtime secrets |
| Attestations/provenance | bind artifact to workflow/repository/commit | guarantee runtime health |
| Rulesets | protected state transitions | continuous runtime convergence |

CodeQL is disabled for this control-plane repository. Default setup and language
auto-detection must remain off so application clients, compiled code, generated
bundles, vendored output, and Swift cannot silently expand the scan or select a
GitHub-hosted runner. Manifest, dependency, image, secret, and runtime security
continue through their dedicated self-hosted policy and observation workflows.

## 7. Static compilation, dynamic observation, AI and executor boundary

### 7.1 Static compiler

Inputs are a signed commit, lockfiles, pinned tool images/actions and immutable
external artifacts. Output is a content-addressed deployment bundle plus:

- input and output digest manifest;
- normalized resource inventory and ownership claim;
- policy/test results;
- SBOM/provenance references;
- expected health contract;
- rollback pointer to the prior accepted digest.

It must be hermetic where practical and must never need cluster write access.

### 7.2 Dynamic observer

The observer is read-only and bounded. It collects:

- installed CRD/API versions and admission results;
- Flux Conditions, observed/applied revisions and inventories;
- Kubernetes object generation/resourceVersion and health Conditions;
- deployment probe results through declared service/public authority paths;
- controller metrics and redacted event summaries.

It writes compact evidence to GitHub deployment/check status and deduplicated
Issues. Raw snapshots, logs and metrics remain in external stores with links.

Observation must span two failure domains:

- an in-cluster observer can efficiently watch Kubernetes and Flux objects;
- an external observer must probe API/public endpoints and node heartbeats when
  the cluster API, CNI or in-cluster monitoring path is unavailable.

The event gateway needs a durable queue, delivery-id dedupe and retry because
GitHub itself or the egress path may be unavailable during the incident. It
must be possible to reconstruct the Issue projection after recovery without
making GitHub the source of the original runtime fact.

### 7.3 AI decision support

AI may classify an event, correlate it with a change, summarize logs, propose a
rollback or draft a PR. It must receive redacted evidence and an explicit tool
catalog. Every result records model/tool version, input evidence references,
confidence and uncertainty.

AI must not:

- write directly to `main` or approve its own PR;
- infer live success from static validation;
- choose an identity across the Re8ch Admin/Re8ch tenant boundary;
- read plaintext Secrets or raw unrestricted telemetry;
- bypass the public-network authority inventory;
- broaden executor permissions or invent a cluster route;
- perform destructive prune/rollback without deterministic policy and required
  human approval.

### 7.4 Minimal executor

The executor is deterministic and contains no policy discretion. Its request is
a signed envelope containing change ID, repository, commit, bundle digest,
target environment/domain, action, expiry and approval evidence. It verifies
GitHub OIDC claims and artifact provenance, checks an allowlist, obtains a
short-lived identity, performs the named action and returns signed evidence.

Allowed primitives should be few:

- read discovery/status/inventory;
- server-side dry-run a fixed bundle;
- request reconciliation of one named Flux object;
- apply/promote one approved digest in one authority boundary;
- suspend/resume one named reconciler as an emergency brake;
- run a declared health probe;
- roll back to the immediately prior approved lock.

It must not accept arbitrary shell, raw `kubectl`, user-supplied credentials,
floating Git refs or unbounded resource selectors.

## 8. Event and state model

```text
PR opened/updated
  -> static compiler check suite
  -> ruleset/CODEOWNERS/merge queue
  -> signed main commit
  -> bundle publication + attestation
  -> environment promotion approval
  -> deployment record = "in_progress"
  -> Flux/source observes digest
  -> apply controller reconciles
  -> observer compares expected digest + health contract
     -> success: deployment = "success", close linked change
     -> transient: deployment remains in_progress, controller retries
     -> actionable failure: deployment = "failure", update deduped Issue
     -> drift: create/update drift Issue; never silently rewrite Git
```

Required correlation identifiers are `change_id`, commit SHA, bundle digest,
GitHub workflow/run/attempt, deployment ID, cluster/environment, Flux object UID
and Kubernetes reconciliation revision. Secrets and full event payloads are
excluded.

## 9. Capabilities that cannot be staticized

The following are hard boundaries, not missing CI features:

1. **Continuous watch and eventual convergence.** GitHub workflows are finite
   runs; turning schedules/webhooks into a permanent retry loop recreates a
   controller less reliably.
2. **Kubernetes admission and defaulting.** CRDs, webhooks, API discovery and
   policy can change after a commit. Offline schemas are necessary but
   insufficient.
3. **Runtime health.** Pod readiness, Job success, network reachability,
   storage attachment and public routing exist only at runtime.
4. **Server-side ownership and conflicts.** Managed fields and competing
   writers are API-server state.
5. **Garbage collection.** Safe prune requires the last successfully applied
   inventory, current ownership and deletion/finalizer behavior.
6. **Helm transactions.** Release Secrets, hooks, tests, rollback and uninstall
   are stateful runtime operations.
7. **Secret use.** Encryption can be checked statically; decryption and provider
   availability are runtime concerns.
8. **External system availability and mutable registries.** A lock can state
   intent but cannot guarantee future reachability or prevent upstream tag
   mutation without digest verification.
9. **Emergency intervention.** An immediate suspend or network safety action
   cannot wait for a normal Git round trip, but must be time-boxed and reconciled
   back through Git afterward.
10. **High-volume telemetry.** GitHub Issues, Checks and Projects cannot be a
    metrics/log/event database.

## 10. Risk register

| Risk | Severity | Failure mode | Control |
| --- | --- | --- | --- |
| Mistaking GitHub for runtime authority | critical | green checks while cluster is broken | runtime evidence required for deployment success |
| Removing Flux too far | critical | no drift repair/prune/health convergence | retain minimal bounded reconciler |
| Dual writer or dual prune owner | critical | resource oscillation/deletion | authority ledger + CI invariant + field manager audit |
| Actions credential compromise | critical | repository-to-cluster escalation | OIDC, protected environment, allowlisted executor, no kubeconfig |
| GitHub outage | high | promotions and collaboration stop | running cluster keeps last state; documented break-glass |
| Actions supply-chain compromise | high | malicious build/check output | SHA pinning, allowlist, provenance, minimal token permissions |
| Code Quality cost misconception | medium | Actions usage rises unexpectedly | budgets, concurrency, path filters; no duplicate analyzers |
| Code Quality false confidence | high | runtime/config defects missed | separate manifest, security and runtime checks |
| Event-to-Issue flood | high | unusable operations queue | dedupe, severity routing, stability windows, external raw store |
| Project becomes second authority | medium | status diverges from Issue/Git | one-way projection with write-back/revert rule |
| AI autonomous mutation | critical | non-deterministic unsafe change | advisory-only, PR boundary, independent approval |
| Secret leakage into evidence | critical | credential exposure in logs/issues | redaction, schema allowlist, secret scanning, no raw payloads |
| GitHub API/rate limits | medium | delayed status and automation | queue, idempotency, backoff, dead-letter and reconciliation |
| Helm-to-rendered migration semantic loss | high | hooks/rollback behavior disappears | per-release qualification; retain Helm when needed |
| Stale offline schema | high | CI accepts object rejected at runtime | pin schema source and perform bounded live dry-run before promotion |
| Dependabot coverage gap | medium | untracked chart/image versions | explicit source catalog plus custom updater only where required |
| Federated domain coupling returns | high | root failure blocks autonomous domains | preserve repository/controller authority boundaries |
| Public network authority violation | critical | wrong R640 route/Gateway advertised | validate against sole inventory authority; runtime probe both paths |

## 11. Migration sequence and gates

### Phase 0 — inventory and baselines

- Inventory every Flux CR, controller version/flags/RBAC, source, artifact,
  owner, dependency, event route, health check, prune inventory and secret ref.
- Measure controller CPU/memory/network/storage, self-hosted runner usage,
  Actions minutes, reconcile latency, failure rate and Issue volume.
- Classify each operation as static, observer, policy/AI or executor.

Exit gate: no resource lacks a recorded authority and rollback owner.

### Phase 1 — static compilation parity

- Reproduce current Kustomize/Helm outputs with pinned GitHub-hosted workflows.
- Compare normalized resources and encrypted payload digests against current
  domain bundles.
- Publish candidate OCI bundles and provenance without changing Flux inputs.

Exit gate: repeated builds are deterministic and differ only by declared input.

### Phase 2 — GitHub admission and analysis

- Make render/schema/policy/ownership checks required.
- Enable dependency review, Dependabot, secret scanning, CodeQL and Code Quality
  according to language/support and eliminate duplicate analyzers after parity.
- Export ruleset/security configuration for review and drift audit.

Exit gate: no existing mandatory signal is lost; usage/cost baseline is known.

### Phase 3 — immutable source promotion

- Convert floating source/chart/image refs to commit/digest locks.
- Let GitHub publish exact bundles; point one canary Kustomization at the locked
  OCI digest while keeping current controller ownership.
- Verify signature, applied digest, inventory, health and rollback.

Exit gate: canary survives GitHub unavailability and controller restart using
the last accepted state.

### Phase 4 — event projection

- Project deployment and Flux failure state into GitHub deployment/checks and
  deduplicated Issues.
- Keep raw events/telemetry external. Validate rate limiting and dead-lettering.
- Replace only GitHub-origin notification Receivers with native GitHub events.

Exit gate: every projected event links to runtime evidence and remains idempotent.

### Phase 5 — controller minimization

- Remove cluster-side build work now proven equivalent.
- Retire notification/image automation components only when no active CR or
  dependency remains.
- For each HelmRelease, explicitly choose “retain Helm lifecycle” or
  “pre-rendered apply”; do not bulk-convert.
- Reduce RBAC, watched namespaces and controller resource requests after load
  measurements.

Exit gate: rollback drill, prune safety proof, zero dual writers, and at least
one full drift/recovery exercise per domain.

### Phase 6 — optional direct minimal executor

This is optional, not the default target. Replace a Flux apply loop only where
the workload is a high-cadence product already assigned to an authenticated
publisher. Shared infrastructure and security guardrails remain Flux-owned.

Exit gate: publisher has bounded SSA ownership, health verification, rollback,
audit, outage behavior and no cluster-scoped or secret-value access.

## 12. Validation and success measures

Architecture acceptance requires:

- 100% desired objects mapped to one active authority and field manager;
- 100% production inputs locked to commit or digest;
- zero plaintext credentials in Git, artifacts, Issues or workflow logs;
- deterministic bundle reproduction from recorded inputs;
- no self-hosted runner use for jobs supported safely on GitHub-hosted runners;
- runtime deployment success based on observed digest and health, not CI alone;
- recovery of a deleted/drifted canary by the retained reconciler;
- tested GitHub outage, registry outage, admission failure and rollback paths;
- controller resource reduction measured against the Phase 0 baseline;
- Actions usage measured separately for Code Quality/CodeQL and repository CI.

## 13. Open decisions before implementation

1. Which GitHub Enterprise plan and organization policies are active for Code
   Quality, code-scanning rulesets, hosted runner sizes and Actions retention?
2. Which current Actions run on GitHub-hosted versus cluster/self-hosted runners,
   and what is their actual CPU-minute/network profile?
3. Which Helm releases depend on hooks, tests, remediation or Helm release
   history and therefore cannot use rendered bundles?
4. Is GHCR reachable from every cluster during a GitHub incident, and what
   local cache/retention guarantee is required?
5. What system persists webhook dedupe, leases and dead-letter items?
6. What is the approved minimal executor implementation and its formal verb,
   namespace and resource allowlist?
7. What stability window closes a reconciliation Issue automatically?
8. Which external registries/package ecosystems are outside Dependabot coverage?
9. How will live Kubernetes/OpenAPI schemas be captured and versioned without
   leaking cluster details?
10. Which federated domain is the lowest-risk canary? Network should not be first
    because its rollback and public-authority blast radius are highest.

## 14. GitHub-native maintenance pilot findings

Inspection and a bounded pilot on 2026-10-22 tested whether GitHub Apps,
Dependabot, Security/CodeQL, Checks and Projects can form the proposed static
maintenance loop for `re8ch/service-backend`.

### 14.1 Observed state

| Capability | Observed state | Consequence |
| --- | --- | --- |
| GitHub App repository events | receives Checks, Issues, PR, review, repository and Status events | sufficient for repository-local intake and response |
| GitHub Projects v2 | organization Project and typed maintenance fields created with scoped operator credentials; the original App still lacks Projects v2 | proves the model while preserving the need for a dedicated maintenance identity |
| Dependabot alerts | API returned active alerts, including High and Medium Vite findings | dependency graph/advisory detection exists |
| Dependabot version config | declared for Actions, Python, npm and Go ecosystems | update cadence and bounded per-directory concurrency are reviewable |
| Dependabot security updates | enabled | security alerts can become update PRs |
| Code Security / code scanning | disabled; default setup and language auto-detection removed | no CodeQL runner is dispatched; any future re-enable requires an explicit cost, scope, capacity, and runner review |
| Secret scanning / push protection | enabled | GitHub rejects supported secrets before they enter normal history |
| Existing Checks | recent control-plane CI and policy-review workflows succeeded | Checks are a viable admission/output channel |
| Existing dependency gate | custom Trivy filesystem scan and reviewed baseline | must be compared with, not blindly duplicated by, Dependency Review |

The pilot created a typed maintenance Issue and Project item. That operator
bootstrap is not the steady-state identity: Project mutations move to the
dedicated Maintenance App before unattended execution.

### 14.2 GitHub App separation

Use three installations instead of one broad identity. Installation tokens are
short lived; private keys remain outside Git and each App is installed only on
the repositories it needs.

| App | Repository permissions | Organization permissions | Events | Explicitly cannot do |
| --- | --- | --- | --- | --- |
| Observer | Actions, Checks, Contents, Deployments, code scanning alerts, secret scanning alerts and vulnerability alerts: read | Projects: read only when portfolio correlation is required | check run/suite, workflow run, deployment/status, security alert events | write Issues, PRs, deployments, repository contents or Kubernetes |
| Maintenance | Contents, Actions, Checks and security signals: read; Issues and Pull requests: write | Projects v2: write | issues, issue comments, PRs, reviews, Checks and security/Dependabot signals | merge protected PRs by bypass, write deployments, obtain Kubernetes credentials |
| Executor | Contents, Pull requests and Actions: read; Deployments and Checks: write only if those are the approved dispatch/result channels | none | approved deployment/workflow/check events | decide remediation, mutate Projects, edit source, broaden RBAC or accept arbitrary commands |

Kubernetes observation is not granted by a GitHub permission. An external
observer with read-only Kubernetes RBAC publishes normalized, redacted facts as
Checks or Issue evidence using the Observer identity. The Executor receives
only operation IDs from the reviewed operation catalog; its cluster credential
is separately scoped and never becomes an App permission or repository secret
available to pull-request code.

Routine reconciliation observations do not request a human review and do not
create one Issue per sample. They update deduplicated state and appear in a
weekly maintenance summary. Immediate human notification is reserved for a
new critical security signal, a failed protected deployment, loss of
observation beyond its SLO, or a proposed change to an authority path covered
by `CODEOWNERS`.

### 14.3 Minimum enablement order

1. Grant the GitHub App organization Projects v2 read/write permission and
   subscribe to Project/item changes. Keep repository Issues, Checks, PR and
   Contents permissions narrowly scoped.
2. Define one Project maintenance policy: required fields, state transitions,
   risk/owner mapping, dedupe key and automation identity.
3. Add repository-owned Dependabot configuration for supported ecosystems;
   enable security updates only after PR concurrency/grouping is defined.
4. Keep CodeQL disabled for the control-plane repository. If a later threat
   model justifies it, introduce it only through a separate reviewed workflow
   with default setup disabled, explicit language/path allowlists, self-hosted
   runner labels, cadence, timeout, and a compute budget.
5. Enable secret scanning and push protection before granting any automation
   Contents write access.
6. Run the first Issue pilot from an actual finding. The App must upsert by a
   stable key, add the Issue to the Project, populate fields, and link the
   originating alert/check.
7. Let Dependabot or an agent open a PR; require Checks and review. Merge is the
   desired-state transition. Runtime verification, when applicable, updates the
   Issue before closure.

### 14.4 Intended maintenance loop

```text
Dependabot / CodeQL / Code Quality / repository policy Check
                    │
                    v
         GitHub App normalize + dedupe
                    │
                    v
       Issue + Project fields + evidence link
                    │
          approved remediation class
          ┌─────────┴─────────┐
          v                   v
  Dependabot update PR     AI draft PR
          └─────────┬─────────┘
                    v
        ruleset + Checks + CODEOWNERS
                    │
                    v
             signed main commit
                    │
                    v
       static completion or runtime verifier
                    │
                    v
             Project/Issue resolved
```

This loop handles repository maintenance and GitOps declaration updates. It
does not replace Flux/Kubernetes observation, Helm transactions, live admission,
prune or runtime rollback.

## 15. Upstream references

- [Flux GitOps Toolkit components](https://fluxcd.io/flux/components/)
- [Flux end-to-end architecture](https://fluxcd.io/flux/flux-e2e/)
- [Flux source controllers](https://fluxcd.io/flux/components/source/)
- [Flux Kustomize controller](https://fluxcd.io/flux/components/kustomize/)
- [Flux Helm controller](https://fluxcd.io/flux/components/helm/)
- [Flux notification controller](https://fluxcd.io/flux/components/notification/)
- [Flux image automation controllers](https://fluxcd.io/flux/components/image/)
- [Flux optional components](https://fluxcd.io/flux/installation/configuration/optional-components/)
- [GitHub Code Quality enablement and Actions requirement](https://docs.github.com/en/code-security/how-tos/maintain-quality-code/enable-code-quality)
- [GitHub Code Quality CodeQL analysis](https://docs.github.com/en/code-security/reference/code-quality/codeql-detection)
- [GitHub code-scanning setup types](https://docs.github.com/en/code-security/concepts/code-scanning/setup-types)
- [GitHub code-scanning merge protection](https://docs.github.com/en/code-security/concepts/code-scanning/merge-protection)
- [GitHub deployment environments](https://docs.github.com/en/actions/concepts/workflows-and-actions/deployment-environments)
- [GitHub Projects fields](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields)

## 16. Revisit triggers

Revisit this architecture when Flux adds/removes a GA controller or API,
GitHub changes Code Quality execution/plan requirements, a domain changes
authority, a second cluster is added, GitHub/registry outage objectives change,
or measured controller/Actions cost invalidates the assumed split.
