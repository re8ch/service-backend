# Flux federation operating model

This directory records the reviewed authority split between the main Flux and
four autonomous controller stacks. `authority.yaml` is the machine-readable
ledger. The generated cluster manifests live under
`clusters/re8ch/flux-federation`.

All four domain controller stacks are active and autonomous. The main Flux
continues to install their controllers, namespaces, baseline NetworkPolicy and
bounded RBAC, but it does not reconcile their domain repositories or workload
inventories. A main Flux source or Kustomization failure must not stop an
already running autonomous stack.

The current authorities are:

| Domain | Namespace | Repository |
| --- | --- | --- |
| Agent | `flux-agent-system` | `re8ch-ai/agent-gitops` |
| Identity | `flux-identity-system` | `re8ch/identity-gitops` |
| Observability | `flux-observability-system` | `siren-observer/observability-gitops` |
| Network | `flux-network-system` | `pillars-infra/network-gitops` |

## Bootstrap and replacement

1. Merge a domain repository bootstrap pull request and record the resulting
   `main` SHA in `authority.yaml`.
2. Merge the service-backend bootstrap after CI passes. Verify the four
   controller Deployments in the domain namespace before activating its
   registration.
3. Through the audited control-plane path, run
   `bootstrap-shared-secrets.sh`. It copies the existing SOPS private key and
   OCI reader Secret without placing plaintext in Git.
4. Unsuspend only the domain `GitRepository` and verify signature, revision and
   artifact fetch. Keep its Kustomization suspended until the ownership cutover.

## Per-domain cutover

Perform these steps in `observability-metering`, `agent-runtime`,
`identity-access`, then `networking` order. One reviewed change set must not
contain more than one domain cutover.

1. Record the old `flux-system/reconcile-<domain>` revision, inventory and
   health snapshot. Confirm the new repository lock matches that bundle.
2. In a signed service-backend PR, add the matching
   `guardrails/<domain>.yaml` to the federation kustomization and set the old
   reconciler to `suspend: true`. Keep its declaration for rollback.
3. After the freeze is Ready, enable the autonomous Kustomization with
   `prune: false`. Compare old and new inventories and prove there is no second
   active writer.
4. In a second signed PR, set autonomous `prune: true`, remove the old
   Kustomization from the active manifest, and update `authority.yaml` to
   `state: autonomous` with the merged repository lock.
5. Retain the frozen old declaration in migration history for one rollback
   window; do not resume it while the autonomous owner is active.

Identity and Agent product Deployments already owned by their publishers or
`control-plane-direct` remain excluded. The autonomous repositories own only
their compiled platform guardrails and allowed shared runtime components.

## Steady-state rules

- Each domain controller watches only its own controller namespace.
- An object has at most one active `prune: true` owner.
- Git and OCI credentials are domain scoped; shared SOPS/OCI reader material is
  copied into each controller namespace without entering Git.
- Main Flux may suspend a domain as an emergency brake, but cannot silently
  become its workload owner.
- Network Flux receives named, audited cluster-scoped permissions. It cannot
  create or delete arbitrary admission policies or escalate unrelated RBAC.
- Emergency live objects are temporary incident controls. Durable state must be
  reviewed in the owning GitOps repository, and the temporary object must be
  removed after verification.

## Verification and rollback

Run `python operations/flux-federation/validate.py` and render the root before
every change. Positive RBAC tests impersonate each domain's kustomize and Helm
service accounts against its recorded resource set. Negative tests must cover
other controller namespaces, `flux-system`, global Secrets and unlisted
cluster-scoped resources.

For rollback, suspend the autonomous Kustomization before restoring the old
reconciler at its recorded revision. Never allow two `prune: true` owners for
one object. A controller rollback does not authorize moving an application
runtime back under Flux.
