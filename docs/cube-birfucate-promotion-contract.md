# CubeSandbox promotion into the Birfucate resource group

Status: proposed; **not approved for activation**. This document is a
promotion contract, not a declaration that the current `cube-trial` installation
is a production Consumable.

## Authority and boundaries

CubeSandbox owns sandbox lifecycle, guest execution and its control-plane state.
AdvancedFabric owns cluster network transport and routing; Cube is a consumer of
that fabric, not a new router or public IP authority. Runtime Anatomy describes
and verifies Cube runtime components and durable state, but does not take over
Cube lifecycle or the Ceph storage controller. Birfucate owns the Consumable,
claim, service-instance and UsageEvent relationship, not native runtime access.

The intended resource graph is:

```text
Birfucate sandbox.runtime.v1 service instance (Cube provider)
  ├─ Cube sandbox/session and control-plane runtime references
  ├─ network.tenant.v1 attachment → AdvancedFabric/Cilium path and Hubble evidence
  ├─ storage.volume.v1 claim → CephFS/RBD identity and retention policy
  └─ Runtime Anatomy Runtime → observed Cube component graph and commits
```

Use the same `tenant_ref`, `service_instance_ref`, `resource_ref`, `cluster_ref`
and `region_ref` throughout the four planes. A Cube sandbox ID is not by itself
a tenant identity or billable service instance. Native Cube authorization and
Kubernetes RBAC remain the final access checks.

## Network consumption

1. Keep Cube guest CIDR `192.0.2.33/18` separate from Kubernetes Pod/Service
   CIDRs and AdvancedFabric VIP ranges. Route-aware `cube-router` remains the
   guest-to-node implementation; do not advertise the guest CIDR as a cluster
   PodCIDR, add a host bypass route, or mutate the public authority.
2. Represent each granted Cube network attachment as a Birfucate
   `network.tenant.v1` dependent resource with explicit destinations, policy,
   bandwidth/quota and source identity. The network controller, not Cube, owns
   Cilium/Gateway/AdvancedFabric changes. The default remains deny for private
   ranges; DNS and optional proxy endpoints are exact grants.
3. Attribute Hubble flow/egress evidence to the sandbox's service instance.
   AdvancedFabric path health and route changes are dependency evidence, not
   proof that an individual guest can reach its destination. Verify from a
   disposable guest, including denial tests, before changing publication.
4. Keep the existing Dex-protected dashboard separate from the tenant-safe
   sandbox API. A working browser terminal does not establish CLI/API readiness.

## Storage and Anatomy

1. Inventory the existing Cube MySQL/Redis/control-plane PVCs by UID, StorageClass,
   reclaim policy and backup evidence. Retain them during migration. These are
   platform control-plane state, not tenant workspaces or automatically billable
   guest storage.
2. Allocate tenant workspace claims through `storage.volume.v1`, recording a
   stable claim/volume reference and retention class. Do not treat the node-local
   `/data/cubelet-xfs.img` loopback image as replicated durable state. Guest
   workspace recovery requires a separately proved mounted-volume or export
   adapter; CephFS control-plane PVCs do not prove it.
3. Register Cube with Runtime Anatomy first as `ExternalLifecycle` and
   **Observed** components: Cube control plane, sandbox instance, network context,
   credential references and storage references. Unknown capture/restore
   capability must remain unknown. Do not enable the current `cubeSandbox`
   integration or label any Cube commit restorable until an adapter demonstrates
   capture, isolated materialization, fencing and restore of a disposable guest.
4. Keep credential values out of Anatomy commits and Birfucate UsageEvents.
   Restore resolves fresh server-side references rather than replaying secrets.

## Birfucate promotion sequence

| Gate | Required evidence | Activation allowed after gate |
| --- | --- | --- |
| 1. Runtime | Live Cube API, master, node, proxy, database and CLI ready; authenticated create/exec/pause/resume/delete smoke, including failure path | Publish provider health, not a tenant grant |
| 2. Network | AdvancedFabric path health, exact-policy guest ingress/egress and deny tests, per-instance Hubble attribution | Grant scoped `network.tenant.v1` attachments |
| 3. Storage | Control-plane backup/restore drill; tenant workspace claim and retention drill; dedicated XFS and Cilium compatibility verified | Grant scoped `storage.volume.v1` claims |
| 4. Anatomy | Component inventory, immutable manifest, snapshot/restore/fencing drill, truthful consistency and restorability status | Enable Cube integration with the proved capability subset |
| 5. Product | Tenant isolation, quota, TTL, revocation, audit, UsageEvent correlation, SLO/alerts and signed GitOps/CI review | Move Cube out of `evaluation-only`; approve a Cube-backed `sandbox.runtime.v1` plan |

Promotion must not repoint the existing OpenSandbox provisioner in place. Add a
Cube-specific provider/plan behind the same service class, with explicit
provider selection and separate eligibility. Keep pricing unpublished until
metering reconciles against sandbox lifecycle and network/storage dependencies
without double counting. Rollback disables new grants and drains active
sandboxes under retention policy; it never deletes retained PVCs or shared CRDs.

## Current blocking observations

On 2026-09-22, the platform service catalog for `cube-trial` returned only
`oauth2-proxy-cube` (1/1) and `brand-codex-tool-proxy` (0/0), not the Cube core
workloads. That catalog is a sanitized observation surface, so its omission
requires a native live-inventory check rather than proving deletion. The
namespace remains unclassified in that surface. In Git, Cube is still
`evaluation-only`, the Anatomy Cube integration is disabled with dedicated-XFS,
Cilium-compatibility and checkpoint/restore blockers, and the trial runbook
explicitly excludes the production tenant runtime. These discrepancies must be
resolved before changing a production lifecycle field or cluster route.

The next operational step is a credential-free native inventory and failure
diagnosis through the Cloud Platform authority, followed by a disposable CLI
smoke. Record operation IDs and timestamps. Do not use the historical generated
inventory as proof of present readiness.
