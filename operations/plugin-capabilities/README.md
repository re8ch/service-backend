# Cluster plugin OpenAPI capability inventory

Target UI: **RE8CH management tools directory**, as confirmed by the user.
This work is not a Headlamp-only or Codex-only catalog change.

The captured native contracts contain **578 HTTP operations**. These are actual
Kubernetes OpenAPI 3 documents, including schema references and controller status
subresources. They are not synthesized REST wrappers around MCP. The live
`/openapi/v3` index contained 125 entries on 2026-10-04, of which 98 are
API group/version documents and 27 are discovery/metadata entries. The eight
captured here include the initial named-plugin set plus Ceph CSI and ObjectBucket;
90 other group/version schemas are not captured. The discovery report preserves
the earlier six-schema snapshot; the declaration audit also computes current coverage. `live-discovery-report.json` preserves this full distinction,
without equating an API group with an installed plugin or granted permission.

| Native group/version | Operations | Plugin coverage | Schemathesis enumeration | Live HTTP / UI semantics |
| --- | ---: | --- | --- | --- |
| csi.ceph.io/v1 | 55 | Five Ceph CSI resource kinds | Passed | Not run |
| objectbucket.io/v1alpha1 | 21 | ObjectBucket and ObjectBucketClaim | Passed | Not run |
| anatomy.re8ch.com/v1alpha1 | 77 | Seven Runtime resource kinds | Passed | Not run |
| ceph.rook.io/v1 | 209 | Nineteen Rook resource kinds | Passed | Not run |
| networking.advfab.org/v1alpha1 | 78 | Eight advfab resource kinds | Passed | Not run |
| networking.advfab.org/v1alpha2 | 20 | NetworkPathAssessment, RouterOSNode | Passed | Not run |
| networking.re8ch.com/v1alpha1 | 98 | Legacy advfab, PublicEdge, MobileEgress | Passed | Not run |
| networking.re8ch.com/v1alpha2 | 20 | Legacy NetworkPathAssessment, RouterOSNode | Passed | Not run |

`native-operation-matrix.json` has one row per method/path/operationId, resource
kind, native action, and explicit registration and test states. Each source
points to its original OpenAPI file and Cloud Platform audit reference. Schema
snapshots describe APIs; they contain no live resource objects or telemetry.

## Implemented registration (pending release)

The native backend implements four discovery/read entry points:
`plugin.capabilities.list`, `plugin.openapi.get`, `plugin.operations.list`, and
`plugin.operation.read`. The generator also registers **197 individually named
GET tools** from the eight captured schemas. The same definitions drive backend
MCP schemas, the two self-service allowlists, and management directory metadata.
Run `python operations/plugin-capabilities/generate.py` to regenerate them.

Reads require platform administrator authorization. Each generated tool pins its
native operationId and schema digest, validates native path/query parameters,
bounds list/watch responses, and strips credential fields and historical
annotations. Before redaction, JSON bodies are checked against native response
schemas using OpenAPI 3.0 nullable/boundary semantics and local-only references.
Watch validates event envelopes and observed resource bodies; ERROR events fail
the call without returning partial success. `bodyValidation` reports its scope
and object count, independently of HTTP conformance (headers/status are not
retained by the kubectl transport). Schema changes, truncated output, unsafe paths, and writes fail
closed. The **381 mutation operations are not implemented**. Status
writes are controller-owned; other mutations need an explicit authority review,
including distinguishing transient requests from GitOps desired-state changes.

`registry.json` supplies the management directory's actual Stacklok metadata
format, including operation, parameters and explicit `not_run` acceptance states.
The owned UI patch displays those fields and expandable parameter schemas.
A separate `re8ch_capabilities` inventory drives the Native capabilities tab:
all 578 captured Kubernetes operations and all 40 inventoried Mihomo routes stay
visible, including missing adapters and controller-owned writes. The searchable
table displays implementation/deployment, authority, schema provenance and HTTP/UI
acceptance independently. Inventory rows never become callable tool definitions
unless an adapter exists. Conditional Mihomo surfaces and other clusters remain
outside this initial 618-operation inventory, as stated in the UI scope.
`kustomization.yaml` is referenced by the root GitOps configuration, and
`platform/connectool/release.yaml` mounts the generated catalog into the approved
registry. `registry-rollout-values.yaml` documents the source configuration.
`deployment-evidence.json` records the 2026-10-04 authenticated registration
comparison for all 212 tools and representative named reads across eight
Kubernetes groups plus Mihomo. The native and UI images each had two ready
replicas. Registration badges use this historical evidence only while input
schema hashes match; they are not a live health indicator. HTTP and semantic
conformance remain `not_run`. Browser login and full acceptance remain pending.

The discovery index preserves unknown groups as `unclassified`. The native
schema fetch uses exact groupVersion and server-relative OpenAPI URLs; it cannot
fetch arbitrary URLs. Generated registration is implementation evidence, not
proof of live availability or successful invocation.

## Reproduce offline contract enumeration

```sh
uv run --no-project --with schemathesis==4.29.1 python operations/plugin-capabilities/verify.py \
  --report operations/plugin-capabilities/schema-enumeration-report.json
```

The verifier uses real Schemathesis operation parsing and compares the complete
method/path/operationId set with the matrix. Missing, duplicate, unparseable or
extra operations fail. This performs **zero HTTP requests**. Passing this step
must never be labeled live schema conformance, successful tool invocation, or
Stagehand semantic acceptance.

[The Schemathesis CLI](https://schemathesis.readthedocs.io/en/stable/reference/cli/)
runs API tests from these documents and validates responses. The next acceptance
stage needs isolated representative resources, explicit mutation disposition,
authenticated test identities, and response evidence. Existing GitOps authority
still governs infrastructure changes; generated schema availability is not an
exception to the PR/signed-commit rule.

[Stagehand](https://stagehand.dev/) supplies observe/act/extract primitives for
browser workflows. Acceptance must execute the management tools directory flows
and verify backend state, including unavailable backends and failed operations.
Offline schema enumeration cannot satisfy that gate.

The pinned [Stagehand inventory runner](stagehand/README.md) now implements
observe/act/extract navigation and group filtering, with exact rendered-table
comparison against the registry. Its offline tests run in the UI validation
workflow. Live execution requires an approved model connection and an already
authenticated dedicated browser. Missing configuration returns `not_run` and
a nonzero exit. Even a successful inventory navigation run does not establish
tool invocation, backend state or complete semantic acceptance.

## Remaining full-scope work

- Release and verify the management directory integration at `https://tools.re8ch.com`.
  The live directory rejected its canonical origin with `INVALID_ORIGIN`;
  #1350 prepares the origin correction. Its configured Authentik provider is
  intentionally scaled to zero pending canary, so identity availability also
  needs resolution. Component tests do not replace live Stagehand acceptance.
  Authenticated invocation from the directory still requires end-to-end validation.
- Inventory all installed plugins and all connected clusters, beyond these eight
  native API documents. Preserve separate cluster authority and identity.
- Add mihomos controller APIs, public-edge manager APIs, Ceph manager/CLI APIs,
  and semantic workflows not described by Kubernetes CRDs.
- Review and implement operation-level adapters, including mutation planning,
  GitOps handling, controller-owned status, authorization and idempotency.
- Execute live Schemathesis tests and Stagehand workflows, then publish signed
  releases and verify actual registration/calls/UI after rollout.

Identity recovery in another task is coordinated separately. Do not replace or
roll back its tenant-mcp/cloud-platform images while preparing this feature.

## Mihomos native surface (in progress)

`mihomos.workloads.list` is implemented, administrator-only and registered in
both self-service allowlists and management directory metadata. It returns only
fixed Mihomos DaemonSet identity, generation and readiness counters from
`egress-fabric`; it never returns pod configuration, environment or annotations.
Absent optional workloads are reported as missing, not automatically unhealthy.
It is pending release, and does not establish controller API coverage.

`mihomos-native-surfaces.json` records 40 controller method/path pairs from
Mihomo v1.19.29 at commit `e26714a181ac0e2fa803453c0a8e9a9ce94e31cb`, matching
the observed cluster image version. It preserves source file digests and explicit
unimplemented states. This is a source route inventory, **not an OpenAPI schema**.
Debug, UI, configured DoH and build-specific external routers are tracked as
conditional surfaces requiring further enumeration. GET delay/healthcheck/DNS
routes cause network activity and need separate policy review. Streaming and
WebSocket semantics also need distinct acceptance.

## Native runtime release gate

`cloud-platform-release.yml` validates generation, Schemathesis enumeration,
focused tests, and the actual Docker image on pull requests. Its network-isolated
image smoke test checks packaged tool discovery and administrator filtering.
Publication is restricted to validated `main` runs in the production environment.
The existing publisher builds on GitHub runners, emits SBOM/provenance, signs
and verifies the immutable Harbor digest with GitHub OIDC, then uploads the
image reference and SBOM. A separate signed GitOps promotion must update
`platform/cloud-platform/runtime.yaml` and activate the prepared registry source;
image publication alone does not deploy these tools.

Mihomos registration includes 15 tools: workload and controller discovery plus
13 native controller GET operations covering greeting, version, configuration
summary, finite connection snapshots, proxies, groups, rules and providers. Discovery returns reviewed node-proxy pods, UID and readiness. Every
native read revalidates owner, namespace, controller port and Ready state before
using the authenticated Kubernetes Pod proxy. Caller URLs, namespaces, ports and
credentials are not accepted. Native names are validated and encoded as single
path segments. Responses are schema-validated and projected to declared fields;
probe URLs, subscription information and extensible adapter fields are omitted.
The packaged adapters are deployed; see `deployment-evidence.json` for the
limited registration and representative-call evidence. This does not establish
complete controller operation or semantic acceptance.

`operations/cloud-platform/mihomo-controller-openapi.json` is an authored, partial
native contract derived from pinned upstream source, not an upstream-published
document. Schemathesis enumeration checks its 13 operations separately from the
578 Kubernetes operations. HTTP response conformance and Stagehand remain not
run; 27 inventoried controller routes and the conditional surfaces still need
adapters, contracts and acceptance. The generated directory currently contains
212 tools (197 Kubernetes reads and 15 Mihomos tools), pending deployment.

The finite `/connections` adapter sends no WebSocket upgrade or interval. It
preserves IDs, counters, routing chains and selected network metadata while
omitting process paths, user identity, geolocation and unknown fields. Native
null connection slices remain null; truncation fails the whole call. Its
WebSocket mode remains unimplemented. `/configs` exposes a typed scalar summary,
not a configuration export: authentication, TUIC/TUN settings, SS/VMess config,
updater URLs and unknown fields are omitted. `/` returns only the controller
greeting and makes no data-plane health claim. These contracts link the pinned
upstream source files and SHA-256 evidence used to review their response shapes.

`mihomos-finite-read-samples.json` records two untruncated native responses read
through the authenticated platform module. Greeting and an empty connection
snapshot passed the local native-body validator and projection. No raw bodies
are committed; the report retains audit references, schema digest and sample
scope. Nonempty connection records and config credential projection have
synthetic tests only. Deployed tool invocation, HTTP status/header conformance
and Stagehand operation semantics remain `not_run`.

## Declared plugin and cluster audit

`audit.py` reconciles every root PluginDefinition (including list entries), the
implemented directory metadata, and both platform/external cluster inventories.
The deterministic `declaration-coverage-report.json` preserves plugins with no
adapters, unverified CLI image digests, generated tools without root definitions,
and external cluster proposals without proven owner access. It currently lists
13 declared or schema-classified plugin identities and three governed/proposed clusters. This is declaration
coverage, not a scan of all live installations or proof of access.

Mihomos now declares its actual Kubernetes workload source in `egress-fabric`
and the separate partial native controller contract, replacing the unverified
`mihomos-cluster.mihomos.svc/openapi.json` source. PublicEdge's source group is
corrected to `networking.re8ch.com`, matching the captured native contract.
Root PluginDefinition policy/identity and the runtime adapter's administrator
authorization still need full reconciliation before complete publication.

## Anatomy mutation authority evidence

`operations/cloud-platform/anatomy-operation-authority.json` binds the seven
observed Anatomy kinds to a SHA-256 of the checked-in controller source and its
reconciler functions. Operation discovery and the directory use this shared
classification for all 49 Anatomy mutation operations. Status writes remain
controller-owned. Runtime desired configuration requires a GitOps PR; capture,
restore, branch CAS and transient requests retain distinct review requirements.
No mutation executor is enabled by these classifications, and live controller
source parity remains unverified.

The source exposes semantics that native CRUD schemas alone cannot prove:
RuntimeCommit creates snapshots/PVCs/Jobs; RuntimeDiff compares sealed manifests;
RuntimeGarbageCollection currently only reports a DryRun. RuntimeCheckout only
special-cases Plan; other modes materialize state. Several reconcilers return
immediately for terminal phases, including Active RuntimeBranch objects, so a
successful spec patch is not proof that the requested action ran. Source changes
fail the authority-evidence test and require a fresh semantic review.

## Live response-body samples

`live-body-samples-report.json` records six actual, bounded, nonempty list
responses obtained through the authenticated tenant platform module. The local
adapter validator at the recorded commit accepted these responses against the
captured OpenAPI documents. Only operation identities, schema digests, counts
and audit references are retained; raw resource bodies are not committed.
This establishes sampled body compatibility only. Live schema parity, deployed
adapter invocation, full operation coverage, Schemathesis HTTP conformance and
Stagehand semantics are not established by this report.

`live-schema-parity-report.json` subsequently compared all six captured schema
objects and normalized SHA-256 digests with freshly read production documents.
All six matched at the recorded timestamp. The report retains separate native
schema audit references and does not upgrade HTTP or semantic acceptance states.
The earlier body-sample report remains a historical record of its narrower check.

## Ceph CSI and ObjectBucket discovery

The added `csi.ceph.io/v1` and `objectbucket.io/v1alpha1` documents were captured
through the authenticated native backend and enumerated with Schemathesis.
They add 76 native operations to the matrix, including 27 GET operations.
The 27 GET adapters are now enabled for platform administrators and generated
into both self-service allowlists. `storage-read-projection.json` specifies the
reviewed output fields for all seven kinds. It preserves resource identity,
pagination and selected configuration while omitting authentication, arbitrary
additional state/configuration, mount options, container arguments, volumes,
annotations and unknown extension fields. Secret references are names only;
no Secret lookup occurs. Native body validation precedes projection. These
adapters remain pending deployment and live invocation acceptance.

The readable `registry.json` remains the review artifact. Kustomize mounts the
JSON-equivalent compact `registry.runtime.json` under the same `registry.json`
volume key to keep the ConfigMap below its 1 MiB data limit. Generation and CI
check both artifacts; compaction does not drop tools or operation rows.
