# PublicEdge and AmongCluster operating model

This filename is retained for stable links. It now describes the current model;
the superseded 2026-11-22 incident snapshot and completed release checklist were
removed.

## Invariants

1. Every Kubernetes cluster is an autonomous runtime authority.
2. Cloud SSA writes directly to the cluster named by
   `ReleaseContract.spec.target.cluster`.
3. AmongCluster shares services, never Kubernetes control-plane authority.
4. PublicEdge is the preferred service access surface for external and internal
   consumers. Cluster-local addresses are implementation details, not product
   contracts.
5. A service may be reachable from another cluster without moving its workload,
   Node, Secret or SSA ownership there.

## Current cluster roles

| Cluster | Kubernetes control plane | Service role |
| --- | --- | --- |
| `re8ch` | Stable Cloud SSA transport is `kube-api.service.re8ch.com`; the temporary `kube-api.re8ch.com` name and reviewed raw IPs are fallbacks. | Main product and tenant runtime. Publishes and consumes PublicEdge services. |
| `re8ch-atlantic` | Independent API endpoint, CA, OIDC audience, protected GitHub Environment, RBAC and SSA state. It never uses the main-cluster API as a proxy. | Overseas workloads such as Stalwart and their local PublicEdge listeners. Publishes bounded service facts through AmongCluster. |

New clusters follow the Atlantic pattern: define an execution profile, establish
cluster-local OIDC/RBAC and durable state, verify direct Cloud SSA, then allow
contracts to target the cluster.

## Application deployment

1. Build the application artifact once and retain its immutable digest and
   provenance.
2. Declare the actual hosting cluster and namespace in the ReleaseContract.
3. Select that cluster's protected Environment and OIDC audience.
4. Probe the cluster's ordered API transports and validate the matching TLS
   identity.
5. Verify the signed bundle and apply it with the contract's bounded SSA field
   manager.
6. Read back accepted/applied state and workload health from the same cluster.
7. Publish a PublicEdge service contract when other users or clusters need the
   service.

Do not deploy an Atlantic workload into `re8ch` merely to simplify management,
and do not apply an Atlantic object through a main-cluster service account.

## Service sharing

AmongCluster may exchange stable service identity, protocol, port, endpoint
locality, health, freshness, capacity, traffic budget and path class. The
receiving cluster applies its own admission and routing policy.

It must not exchange kubeconfigs, bearer tokens, Secrets, arbitrary Kubernetes
objects, remote Node ownership or unrestricted proxy access. Loss of the hub or
another cluster must not prevent a cluster from serving its local endpoints.

PublicEdge owns the public hostname, TLS/listener policy, regional candidate
selection and bounded forwarding. Internal consumers should use that same
service hostname unless a reviewed service contract requires a private
AmongCluster path. This makes the public contract testable and avoids hidden
dependencies on cluster DNS, PodCIDRs or service meshes.

## Verification

Deployment evidence and service evidence are separate:

- Cloud SSA: signed source, exact digest, target cluster, selected endpoint,
  accepted/applied state, managed fields and workload health.
- AmongCluster: publisher identity, freshness, path eligibility and withdrawal
  behavior.
- PublicEdge: DNS answer, TLS, protocol-specific health and regional behavior.

A successful Pod readiness check does not prove PublicEdge, and a working
PublicEdge route does not prove that the desired release reached the correct
cluster.

For incident recovery procedures, use
[publicedge-dns-recovery-runbook.md](publicedge-dns-recovery-runbook.md). The R640
public authority remains
`operations/network/manifests/network/public-mesh/inventory.json#r640.publicAuthority`.
