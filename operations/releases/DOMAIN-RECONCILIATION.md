# Flux domain reconciliation

The cluster root is a bootstrap and coordination boundary, not the owner of
product workloads. Five business domains still reconcile independently from
the verified `service-backend` Git source:

| Domain | Runtime owner |
| --- | --- |
| data-storage | `Kustomization/reconcile-data-storage` |
| commerce-billing | `Kustomization/reconcile-commerce-billing` |
| operations-ui | `Kustomization/reconcile-operations-ui` |
| delivery-products | `Kustomization/reconcile-delivery-products` |
| tenant-runtime | `Kustomization/reconcile-tenant-runtime` |

`networking`, `identity-access`, `agent-runtime`, and
`observability-metering` have completed their transfer to independent Flux
controller stacks and private desired-state repositories. Their repositories,
controller namespaces, locks, and current `autonomous` state are authoritative
in `operations/flux-federation/authority.yaml`. Frozen legacy declarations are
rollback evidence only and must not be resumed while an autonomous owner is
active.

The reconcilers have no `dependsOn` edge to `Kustomization/service-backend`.
Once bootstrapped, a render or apply failure in one domain does not prevent a
signed revision from being attempted by the other domains. Each domain prunes
only its own inventory. The root remains `prune: false` so it cannot delete an
object while ownership is transferred.

## Authoring and build boundary

Existing reviewed Kustomize roots remain in place. A domain's
`source/kustomization.yaml` composes those roots during review. Run:

```sh
python operations/releases/compile_domain_bundles.py
```

This writes the domain-local `resources.yaml` consumed by Flux. CI pins the
Kustomize version and rejects stale bundles. Runtime Flux retains its root-only
file loader; it does not need permission to traverse the repository. Encrypted
SOPS resources stay encrypted in the generated bundle and each encrypted
resource has exactly one declared domain owner.

## Authority boundary

GitOps owns deployable code, immutable image digests, routing, policy, Secret
references, and emergency platform brakes. It does not own ordinary business
state such as whether a deposit channel is open, a catalog item is active, or
an account is entitled. Those decisions belong in an authenticated service API
and its audited database. A platform brake may close a channel during an
incident, but normal operators must not need a Git commit to reopen it.

That is only the first split. `runtime-authority.yaml` is the reviewed authority
ledger for workloads themselves. An externally consumed product with a high
change cadence belongs to a product-owned release publisher, not to Flux. The
initial migration set is commerce/payment, the user-facing identity runtime,
and ConnecTool. Flux keeps their slow-moving platform guardrails: namespaces,
quota, NetworkPolicy, bounded RBAC, Secret material and references, shared edge
and certificate authority, database routing and an emergency brake.

The three product runtimes are excluded at render time from the domain
bundles. `reconcile-commerce-billing`, `reconcile-identity-access`, and
`reconcile-agent-runtime` continue to own their platform guardrails, but their
inventories do not contain the Commerce or payment-registry Deployments, the
auth-gateway or User Hub Deployments, or ConnecTool's release objects. During
the interim `control-plane-direct` phase, reviewed control-plane operations are
the sole runtime writer. The final state replaces that manual writer with each
product repository's authenticated publisher; it does not put the runtime
objects back under the service-backend Flux source.

The ledger deliberately distinguishes target authority from current authority.
It is unsafe to remove an active reconciler without establishing an explicit
replacement owner and a rollback point. Until the authenticated publisher is
available, `control-plane-direct` entries use the audited control-plane path.
This is a migration state, not permission for new product behavior to be added
back to Flux.

### Authority selection rule

| Workload characteristic | Authority |
| --- | --- |
| External users and high change cadence | product release publisher |
| Ordinary business state | authenticated application API and audited database |
| Shared infrastructure or security guardrail | Flux |

Product publishers use a namespace and resource allowlist, server-side apply
ownership, immutable signed artifacts and append-only release evidence. They do
not receive Secret values, cluster-scoped RBAC, Gateway or certificate
authority, or database administration. A long-lived kubeconfig is not an
acceptable substitute for the publisher.

The legacy `domain-*` OCI reconcilers and the three `observability-*`
reconcilers are retained in a suspended state for rollback visibility. Do not
resume one while its replacement `reconcile-*` owner is active.

## Rollback

1. Suspend only the affected `reconcile-<domain>` Kustomization.
2. Restore or resume that domain's prior reconciler after confirming its source
   revision and inventory.
3. Never enable two pruning owners for the same object.

Because the root does not prune, a root failure cannot garbage-collect domain
workloads. Rollback remains a reviewed, signed Git change.

## CNI and node availability rule

Network Flux owns the existing `kube-system/cilium` Helm release; it must never
install a second release. The vendored chart and runtime images are immutable
and digest locked. Chart reconciliation follows `ChartVersion`, not every Git
revision, and an unrelated Network GitOps commit must not roll all Cilium
agents.

Cilium is node-level infrastructure, so a deliberately offline or failed
worker is a node-health incident, not a reason to roll back already converged
cluster-wide CNI resources. Helm upgrades therefore do not wait for every
DaemonSet pod and do not retry a cluster-wide rollback loop. Per-node readiness
remains visible through Kubernetes and observability alerts.

Gateway readiness is not accepted from `Programmed=True` alone. Production
validation must include a TLS/HTTP probe through both the service VIP and the
declared public authority path. The October 2026 R640 recovery and its durable
checks are recorded in
[`operations/network/docs/r640-cilium-gateway-recovery-2026-10-19.md`](../network/docs/r640-cilium-gateway-recovery-2026-10-19.md).
