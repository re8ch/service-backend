# Internal API capability review

Date: 2026-11-25

Status: initial evidence baseline; implementation remains distributed across
the owning repositories.

## Executive decision

RE8CH needs an internal capability model before it needs a larger external MCP
catalog.

The intended chain is:

```text
product source and Kubernetes APIs
  -> repository-owned API models
  -> reviewed internal capability catalog
  -> Scalar aggregation and documentation
  -> typed domain adapters where raw HTTP is insufficient
  -> ToolHive publication policy
  -> external Agent MCP clients
```

Scalar is the aggregation and discovery layer for internally expressed APIs.
ToolHive is the Agent-facing MCP gateway. Neither is the original authority for
business behavior, Kubernetes reconciliation or tenant ownership.

An API model is not only an OpenAPI file. The internal catalog must also model
Kubernetes CRDs, asynchronous events, streaming protocols, command workflows
and existing MCP-native capabilities. OpenAPI is the preferred contract for
bounded HTTP request/response APIs; AsyncAPI, CloudEvents schemas, protobuf,
GraphQL schema, CRD OpenAPI and typed workflow contracts remain valid first-class
sources when they match the runtime.

## Evidence and scope

This review used:

- workspace doctor result `healthy: true`, 62 catalog modules, audit
  `audit://workspace/op_81aebb3100164573a3509eaf8a32ea0a`;
- live RE8CH Kubernetes reads through the authenticated Cloud Platform MCP;
- every catalog module's GitHub repository and the gitlink SHA locked by
  `github/main`;
- GitHub repository-tree reads at those locked SHAs, without initializing
  submodules in the read-only anchor;
- the current tenant service catalog and the ToolHive CR instances;
- local `/Volumes/A1` repositories only as supplementary discovery evidence,
  never as GitHub authority.

The repository scan classified filenames and repository structure. A route or
handler match indicates implementation evidence, not a complete public API.
Conversely, runtime-generated schemas may exist without an obvious filename.
Each candidate still needs owner confirmation and contract extraction.

## Live cluster baseline

The cluster reported Kubernetes server `v1.36.0-rc3+k3s1`. The live inventory
contained:

| Surface | Observed count |
| --- | ---: |
| Deployments | 326 |
| StatefulSets | 21 |
| DaemonSets | 44 |
| Controller workloads total | 391 |
| Services | 392 |
| Ingresses | 67 |
| HTTPRoutes | 141 |
| Gateways | 11 |
| Network publication objects total | 611 |
| CRDs | 283 |
| CRD API groups | 46 |

At scan time, 54 controller workloads had Ready counts different from desired
counts: 38 Deployments, two StatefulSets and 14 DaemonSets. This is availability
evidence only; intentionally dormant resources require separate classification.
The read is auditable at
`audit://operations/op_4d605e03ef9445819486dfe9b5da2574`.

The largest controller namespaces included `kube-system`, `egress-fabric`,
`observability-system`, `toolhive-system`, `qianwen-ops`, `cube-trial`,
`rook-ceph`, `payments`, `ai-operations`, `nuclio`, `harbor`, `auth`, identity,
billing and tenant infrastructure. The internal API program therefore cannot be
limited to product REST endpoints.

### Native API domains already present

The 283 CRDs include substantial domain APIs:

| Domain | API evidence |
| --- | --- |
| Network and edge | 25 Cilium CRDs, 10 `networking.re8ch.com`, eight `networking.advfab.org`, Gateway API, peering APIs |
| Storage | 19 Rook/Ceph CRDs, CSI Ceph, object buckets, volume and group snapshots |
| AI tools | 14 ToolHive CRDs including MCPServer, remote proxy, registry, tool config, vMCP and composite tools |
| Scheduling | Kueue plus six `scheduling.re8ch.com` CRDs |
| Policy | Kyverno policies, reports and admission APIs |
| Observability | 25 VictoriaMetrics CRDs and four OpenTelemetry CRDs |
| Tenancy and FinOps | tenancy, finops, governance and conformance API groups |
| Runtime and workspace | Anatomy, Mitos, OpenSandbox, Dataspace and workspace APIs |
| Serverless | Nuclio functions, projects, API gateways and function events |
| Devices | device and voice gateway APIs |
| Delivery | static release and release operation APIs |

Kubernetes generic access is not a sufficient internal model for these domains.
Each domain needs curated operation semantics, status interpretation, mutation
policy and ownership boundaries.

### ToolHive publication is currently a small subset

The live ToolHive resources were:

| Kind | Count | Names |
| --- | ---: | --- |
| MCPServer | 4 | cloud-disk, observe, operate and admin internal backends |
| VirtualMCPServer | 4 | tools, operate, admin and tenant publications |
| MCPServerEntry | 5 | admin, observe, operate and tenant backend entries |
| MCPToolConfig | 4 | public-read, observe, operate and admin filters |
| MCPRemoteProxy | 0 | none |
| VirtualMCPCompositeToolDefinition | 0 | none |

This confirms the architectural distinction: ToolHive is a controlled
publication surface, not the internal inventory of all cluster and application
capabilities.

### Tenant service model

The current service catalog expresses six higher-level services:

- model gateway;
- shared PostgreSQL;
- Grafana access;
- Harbor registry access;
- Stalwart collaboration;
- sandbox runtime.

These service classes are useful domain models and should be linked to their
underlying API contracts, plans, bindings, meters and operational evidence.
They must not be flattened into raw provider endpoints.

## Catalog repository review

The 62 modules cover owned, collaboration and read-only upstream repositories.
The locked-tree scan found a large implementation-to-contract gap.

### Explicit contract evidence

| Repository/module | Current explicit model | Assessment |
| --- | --- | --- |
| `re8ch/cloud-functions` | Three identity OpenAPI documents; many Nuclio functions and an MCP gateway | Strongest owned OpenAPI starting point, but function coverage is incomplete |
| `netbirdio/netbird` | OpenAPI plus protobuf | Upstream authority; reference only, do not fork as RE8CH source of truth |
| `modelcontextprotocol/registry` | Registry OpenAPI and JSON schemas | Upstream registry contract; not an application capability model |
| `advanced-fabrics/core` | CRD manifests and Helm values schema | Native Kubernetes model exists; needs curated semantic operations |
| `among-clusters/among-clusters` | CRD, internal API implementation and values schemas | Extract HTTP contract and preserve sovereign/peering semantics |
| `re8ch/product-public-data` | Public snapshot JSON Schema | Data contract exists, not an operational API contract |
| `re8ch/thiking-machine` | Scenario JSON Schema | Domain data model exists, API boundary not yet expressed |

No catalog module exposed an obvious AsyncAPI document. GraphQL and protobuf are
also rare in the owned catalog. This does not mean those styles are forbidden;
it means event and streaming behavior is currently under-modeled.

### High-priority repositories with implementation but weak contracts

| Repository/module | Evidence | Required next model |
| --- | --- | --- |
| `re8ch/cloud-functions` | More than 150 function/API-related files | Complete per-domain OpenAPI and event schemas; split identity, commerce, payment, tenant, registry and operational APIs |
| `Rchais/Artchais-MicroServices` | Dozens of Spring controllers and MCP implementation files | Generate/review Spring OpenAPI; separate customer, admin and Agent-only operations |
| `Woomou/career-ops` | Many Next.js API routes and a Go service | Versioned OpenAPI plus workflow/idempotency semantics |
| `re8ch/anycam-backend` | Multiple Nuclio/provider gateway functions | Model inference, job state, media references and provider error normalization |
| `re8ch/phonaid-service` | Python router and schemas | Export and pin OpenAPI; add telephony event model |
| `re8ch/farseer` | Go API entry point | Add explicit versioned contract and tenant boundary |
| `re8ch/ledger` | Functions and an MCP plugin/RPC catalog | Establish accounting domain API first; derive MCP from reviewed operations |
| `re8ch/2woodblog` | Cloud/edge API functions | Separate content management mutations from public reads |
| `public-edge/mihomos-cluster` | Controller repository but no obvious checked-in API model | CRD and controller API contract, per-cluster targeting and safe network mutations |
| `bifurcate-systems/bifurcate` | Metering implementation without obvious API document | Usage event, meter, reconciliation and immutable evidence contracts |

Mobile apps, websites, games, brand repositories and pure data/model assets do
not automatically need server APIs. They should instead declare which internal
APIs they consume and which domain schemas they own. Avoid inventing an API only
to satisfy catalog uniformity.

### Repositories outside the 62-module catalog

Supplementary local discovery found API-bearing repositories not represented as
distinct catalog modules, including:

- `advanced-fabrics/api`, with OpenAPI and protobuf evidence;
- `re8ch/hyperswitch`, with extensive OpenAPI, protobuf and route evidence;
- `re8ch/cloud-disk-gateway`, with an MCP implementation;
- `public-edge/public-edge-manager` and several infrastructure GitOps
  repositories;
- product repositories such as Cardcat, video-generator and Toonflow with many
  route or MCP implementation files.

Local directory presence is not authority. Each candidate must be reconciled
against GitHub ownership, lifecycle, deployment and the catalog before it can
enter the internal API inventory. Duplicate worktrees and the GitLab historical
remote must be excluded.

## Target internal capability model

Every capability should have one record with these layers:

```yaml
identity:
  capabilityId: stable.domain.version
  ownerRepository: owner/repository
  sourceRevision: git-sha
authority:
  kind: http | kubernetes | event | graphql | grpc | cli | mcp
  runtime: service-or-controller-reference
contract:
  source: repository-path-or-reviewed-url
  digest: sha256
  operations: [stable-operation-ids]
semantics:
  preconditions: []
  postconditions: []
  idempotency: required | intrinsic | unsupported
  compensation: operation-or-none
security:
  audience: internal | tenant | partner | public-agent
  tenantBoundary: description
  roles: []
  secretRefs: []
publication:
  scalar: omitted | indexed | executable
  toolhive: omitted | observe | operate | admin | tenant
evidence:
  contractChecks: []
  authorizationChecks: []
  semanticChecks: []
  liveReadback: reference
```

The record belongs with the product source when that repository owns the API.
This control-plane repository should index identity, ownership, locked revision,
deployment and publication status, not copy every product's OpenAPI document.

## Scalar's role

Scalar should aggregate reviewed OpenAPI documents and provide:

- human API references;
- machine-readable documentation search;
- schema and operation lookup on demand;
- a small search-and-execute MCP surface for internal use;
- installation/environment metadata and effective-state readback.

Scalar must not become a second business configuration authority. Git stores
reviewed non-secret desired metadata; the owning repository stores the API
contract; the runtime service enforces behavior. Scalar credentials remain in
its approved secret system and never in Git.

For hundreds of operations, the client should see a small stable discovery and
execution surface rather than hundreds of eager tools. Operation selection,
environment, authentication mode and executable status must be reviewable.

## ToolHive's role

ToolHive receives only capabilities that passed internal modeling and evidence
gates. It provides:

- Agent-facing MCP protocol and endpoint aggregation;
- OIDC/OAuth resource-server behavior;
- caller-group and per-tool authorization;
- filtering, naming, routing and audit;
- controlled composition for short, deterministic workflows.

ToolHive does not establish API completeness, target-service health, tenant
ownership, idempotency or compensation. Long-running and stateful workflows stay
inside typed domain services and expose plan/apply/status operations.

## Maturity levels

| Level | Meaning | Eligible publication |
| --- | --- | --- |
| L0 discovered | Runtime or source evidence only | None |
| L1 modeled | Versioned contract and owner exist | Scalar search/documentation |
| L2 verified | Schema, conformance and error behavior checked | Internal Scalar execution for reads |
| L3 governed | Auth, tenant, audit, idempotency and mutation policy proven | Internal writes with explicit policy |
| L4 Agent-ready | Tool descriptions, bounded outputs and semantic workflows accepted | ToolHive selected publication |
| L5 externally supported | SLO, versioning, deprecation and support ownership established | External Agent access |

Discovery must never advance a capability automatically. In particular, a CRD,
Ingress, route file or successful `tools/list` is L0 evidence, not L4 proof.

## Work programme

### Phase 1: inventory and ownership

1. Add a repository-owned API capability manifest format and schema.
2. Reconcile the 62 catalog modules, live routes, CRDs and unmanaged GitHub
   candidates into stable capability IDs.
3. Classify repositories as producer, consumer, schema owner, UI-only, asset-only
   or upstream dependency.
4. Record unsupported and intentionally private capabilities explicitly.

### Phase 2: model extraction

1. Complete `cloud-functions` OpenAPI by domain.
2. Export framework-native OpenAPI from Spring, FastAPI and Go routers.
3. Treat CRD schemas as structural contracts and add curated semantic operation
   definitions for Advanced Fabric, PublicEdge, Mihomos, Rook/Ceph, Anatomy,
   scheduling, tenancy and delivery.
4. Add AsyncAPI or CloudEvents schemas for webhook, queue, function-event and
   reconciliation streams.
5. Preserve protobuf and GraphQL where they are the real native authority.

### Phase 3: verification and aggregation

1. Bundle and hash schemas in CI.
2. Validate operation IDs, examples, error envelopes, pagination and bounded
   outputs.
3. Run conformance against deployed canaries with reviewed cleanup contracts.
4. Import only reviewed revisions into Scalar and read back effective state.
5. Build an internal documentation/search installation before enabling API
   execution.

### Phase 4: governed execution and MCP publication

1. Enable internal Scalar execution for verified reads.
2. Require plan/idempotency/authorization evidence for mutations.
3. Place typed adapters in front of APIs whose raw operations are unsafe or too
   low-level for Agents.
4. Publish only task-oriented subsets through ToolHive observe, operate, admin
   and tenant surfaces.
5. Verify runtime state after ToolHive calls; do not treat an MCP success frame
   as the final business postcondition.

## Immediate priorities

1. Define the capability-manifest JSON Schema in this repository.
2. Pilot the model in `cloud-functions`, Advanced Fabric, PublicEdge/Mihomos,
   ledger/payment and tenant self-service.
3. Add `advanced-fabrics/api`, Hyperswitch and cloud-disk-gateway to the catalog
   or explicitly document why they are not independently managed modules.
4. Explain the 54 non-ready controller workloads before advertising their APIs
   as available.
5. Keep the Scalar installation URL as a placeholder until organization,
   environment, access groups and credential references are reviewed.
6. Do not expand ToolHive publication until L2/L3 evidence exists for the
   selected operation set.

## Non-goals

- Do not generate hundreds of MCP tools directly from every route.
- Do not copy product OpenAPI documents into this control-plane repository.
- Do not treat Git declarations as proof of Scalar, ToolHive or runtime state.
- Do not store Scalar PATs, API keys, OAuth tokens or resolved secret values.
- Do not expose generic Kubernetes write access as a substitute for native
  domain operations.
- Do not migrate application resources between Re8ch Admin and Re8ch tenants
  without inventorying their dependencies and authenticated organization.
