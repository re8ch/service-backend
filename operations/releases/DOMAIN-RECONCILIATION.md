# Flux domain reconciliation

The cluster root is a bootstrap and coordination boundary, not the owner of
product workloads. Nine business domains reconcile independently from the
verified `service-backend` Git source:

| Domain | Runtime owner |
| --- | --- |
| networking | `Kustomization/reconcile-networking` |
| identity-access | `Kustomization/reconcile-identity-access` |
| data-storage | `Kustomization/reconcile-data-storage` |
| commerce-billing | `Kustomization/reconcile-commerce-billing` |
| agent-runtime | `Kustomization/reconcile-agent-runtime` |
| observability-metering | `Kustomization/reconcile-observability-metering` |
| operations-ui | `Kustomization/reconcile-operations-ui` |
| delivery-products | `Kustomization/reconcile-delivery-products` |
| tenant-runtime | `Kustomization/reconcile-tenant-runtime` |

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
