# PublicEdge and AmongCluster handoff

Last verified: 2026-11-22 (Asia/Shanghai)

This is the entry point for continuing the PublicEdge, authoritative DNS, and
cross-cluster delivery work. It separates observed state from intended design.
Revalidate every live observation before making a production change.

## Executive summary

The product and GitOps changes have established most of the intended model, but
the 0.8.11 runtime is **not deployed**. The latest protected static release was
rejected by Kubernetes because the rendered DaemonSet used multiple node names
in one `matchFields` expression. A colleague should fix the chart rendering,
release a new immutable artifact, update `pillars-infra/network-gitops`, and run
the protected release again.

Do not solve this by adding live node labels, patching the DaemonSet, or making
external authority health control workload scheduling. Those approaches
recreate the self-lock that caused this incident.

The agreed steady state is one PublicEdge instance per cluster. Each instance
owns its local Nodes and publishes eligible `ServiceEndpoint` candidates through
AmongCluster. AmongCluster exchanges service-level facts, not remote Kubernetes
Node ownership. DNS response maps remain local and source-aware.

## Read in this order

1. [Federated architecture](publicedge-amongcluster-federated-architecture.md)
2. [Recovery and verification runbook](publicedge-dns-recovery-runbook.md)
3. [Authoritative DNS acceleration](authoritative-dns-acceleration-options.md)
4. Existing [R640 Gateway recovery](r640-cilium-gateway-recovery-2026-10-19.md)
5. Existing [RouterOS DNS authority design](routeros-public-dns-authority.md)

## Repository authority map

| Concern | Authority | Notes |
| --- | --- | --- |
| PublicEdge product/chart | `public-edge/public-edge-manager` | Build once; publish immutable image and chart digests. |
| Cluster runtime desired state | `pillars-infra/network-gitops` | Flux/SSA runtime authority. Never duplicate it here. |
| Inventory, handoff, coordination | `re8ch/service-backend` | This repository. No runtime Kubernetes ownership. |
| Cluster reconciliation | Kubernetes + Flux | Runtime truth; a merged PR alone is not deployment evidence. |
| Cross-cluster service exchange | AmongCluster | Share endpoints and health, not Kubernetes Nodes. |
| R640 public delivery | `operations/network/manifests/network/public-mesh/inventory.json` | Only machine-readable R640 public authority. |

## Released and merged changes

### PublicEdge product

| Version | Change | Immutable evidence |
| --- | --- | --- |
| 0.8.9 | External authority evidence and Globalping support | image `sha256:d9dd6af1df4f20f6d2a17eadc32723a530e665b72918b629762d96916744c114`; chart `sha256:fc7f347f57934e0b6b49f4d9cd553cbc03ba520288e11922b60c80ff8bcdbfc3` |
| 0.8.10 | Prefer actual external DNS authorities for Globalping evidence | image `sha256:0ee9e1b226b0309261a21d3d190b0393604ad12b539c86e5b75fa79e4960ec7b`; chart `sha256:2cdc6d4387aafb6bc2b06329c006fa6c7c9c9bfd85a6138aa5e54870d277607a` |
| 0.8.11 | Decouple DNS runtime placement from authority health using `workload.nodeNames` | image `sha256:2780af252f20b1b8b7259c18550fca7d0e99d48369206947ca933b4e678a768c`; chart OCI `sha256:a01f7b1bad51be882f3f0e424bed0670beabab17c5c4ce2e673f3fc586014b19` |

Product PR [#129](https://github.com/public-edge/public-edge-manager/pull/129)
merged as `90b33df9e421b032a1bddd69bac09d7cd6e62938`.

### Network GitOps

PR [#201](https://github.com/pillars-infra/network-gitops/pull/201) merged
as `98be9b451d8fcc7b851a913a558e6749cd67ac96`. It selects PublicEdge 0.8.11,
sets `workload.nodeNames` to `suxunji` and `domain-ex`, keeps R640 first for CN,
and LA first for US West.

The protected `static-release` published bundle
`sha256:f95491de9cdf278381d8ed765cfd78e9072c88c1a959298f4ccb3da730fda36d`.
The reuse/execute run
[#45026534753](https://github.com/pillars-infra/network-gitops/actions/runs/45026534753)
verified its signature and source commit, then failed Kubernetes dry-run:

```text
DaemonSet "public-edge-manager" is invalid:
spec.template.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution
.nodeSelectorTerms[0].matchFields[0].values:
must be only one value when operator is In or NotIn for node field selector
```

Consequences:

- 0.8.11 has not reached accepted/applied equality.
- Do not cite `acceptedDigest == appliedDigest` for 0.8.11.
- Equality was previously proven for 0.8.10 at
  `sha256:a3be382a3b2f7d7c48ab06ab9a0cc5c4fa05d32598a86ad1365520b644954140`.

## Last observed live behavior

These are observations, not promises:

- A CN recursive lookup returned `223.159.210.222` for
  `kube-api.service.re8ch.com`; `/readyz` through R640 returned HTTP 200.
- Direct `/readyz` requests through LA1 `179.255.126.104`, LA2
  `179.253.231.159`, FLK `2.28.106.230`, and KL `198.20.153.89` returned 200.
- Recursive lookups from LA1, LA2, and FLK all returned FLK. This was incorrect
  for US West and was evidence that stale secondary answers were being used.
- Direct DNS requests from overseas to the four CN authority addresses
  `124.220.77.50`, `47.103.114.218`, `101.34.217.95`, and `1.14.174.244`
  were not ready. Fresh Globalping evidence also marked all four unavailable.
- HE secondaries returned the stale FLK answer. A conventional secondary can
  copy zone data, but it cannot execute the custom source-aware response map.
- PublicEdge DNS had desired count zero because external-authority readiness
  was also used as scheduling eligibility. Only stale terminating pods remained.
- `registry.re8ch.com` was itself routed through the failing PublicEdge path,
  while containerd used it as the GHCR mirror. This created a registry bootstrap
  loop. Direct GHCR worked from some nodes but was slow or reset on IPv6.

## Immediate priorities

| Priority | Action | Completion evidence |
| --- | --- | --- |
| P0 | Render one valid node selector term per `workload.nodeNames` entry, or use a supported label selector whose ownership is independent of health. | Chart unit test plus server-side dry-run accepts two nodes. |
| P0 | Release a new immutable PublicEdge image/chart and update the GitOps reference. | Release provenance and exact digests recorded in PR. |
| P0 | Run protected `static-release` with publish (or exact reuse) and `execute=true`. | Workflow success and runtime observation artifact. |
| P0 | Prove accepted digest equals applied digest. | State ConfigMap fields match the intended bundle digest. |
| P0 | Verify every managed field is owned by the networking SSA manager; investigate any remaining Helm ownership. | Object-by-object managedFields report. |
| P0 | Verify CN, US West, EU central, EU north, and SEA response maps from external probes. | UDP/TCP 53 results plus HTTPS readiness per region. |
| P1 | Deploy PublicEdge independently in `re8ch-atlantic`; import only service candidates through AmongCluster. | Both clusters remain functional when the other control plane is unavailable. |
| P1 | Remove the registry bootstrap loop. | A new PublicEdge pod can pull during a PublicEdge outage without manual pre-pull. |
| P1 | Add SLO probes for auth, Headlamp, kube API, DB, and SubAccess. | Alertable success/latency history from multiple regions. |

## Scope boundaries

- Do not disturb `auth.re8ch.com` merely because another service is unhealthy;
  it was confirmed working after the auth repair.
- Do not move remote Nodes between clusters as a PublicEdge mechanism.
- Do not expose cluster credentials through AmongCluster. Export bounded service
  capability and endpoint status only.
- Do not assume a successful HTTP readiness probe proves UDP/TCP 53, OAuth token
  exchange, Kubernetes API streaming, or database protocol health.
- Do not use emergency live patches as desired state. If one is necessary to
  restore service, capture it as evidence and replace it with a reviewed change.

## Required handoff record after the next change

Record the exact source commit, image digest, chart digest, release bundle
digest, workflow URL, accepted/applied digests, managedFields owners, rollout
status, regional DNS answers, service probes, and rollback point. Never record
tokens, kubeconfigs, private keys, or decrypted SOPS content.
