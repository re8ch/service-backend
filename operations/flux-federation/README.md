# Retired Flux federation evidence

This directory preserves the completed transition from autonomous Flux
controller stacks to GitHub Actions and the bounded cloud SSA executor. It is
not an operating model or bootstrap source.

The machine-readable ledger remains
[`authority.yaml`](authority.yaml) because existing contracts reference that
stable path. Read its current fields literally:

- `deliveryMode: github-actions-ssa`;
- `objectOwnership: single-active-writer`;
- every delivery domain has `state: github-actions-ssa`;
- `controllerVersion: retired`.

Fields such as `controllerNamespace`, historical lock SHA and repository
revision describe the cutover baseline. They do not prove that a controller,
Flux source or Kustomization is live.

## Current delivery boundary

Protected GitHub Actions workflows obtain cluster-specific OIDC identities and
submit accepted immutable bundles to the bounded cloud SSA executor. The
executor writes directly to the named cluster API. Independent observers read
back accepted state and runtime health; they do not repair drift or apply
objects.

Current operating guidance is in
[`operations/releases/DOMAIN-RECONCILIATION.md`](../releases/DOMAIN-RECONCILIATION.md).
Current multi-cluster rules are in the
[`multi-cluster federation contract`](../network/docs/multicluster-federation-contract.md).

## Retained historical material

- `migrations/` records old/new inventories, revisions and verified cutover
  evidence.
- generated controller manifests and RBAC document the permissions that were
  removed.
- validation code prevents retired authorities from being presented as active
  or regenerated into the cluster root.

Do not apply the retained manifests, start a controller, suspend/resume a Flux
resource, or use a former controller namespace as a delivery endpoint. Missing
Cloud SSA coverage must be repaired through the reviewed cloud path. Restoring
Flux would be a new authority transfer requiring a separate design and review,
not an incident-recovery shortcut.
