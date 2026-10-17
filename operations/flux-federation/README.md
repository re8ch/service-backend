# Flux federation migration

This directory records the reviewed authority split between the main Flux and
four autonomous controller stacks. `authority.yaml` is the machine-readable
ledger. The generated cluster manifests live under
`clusters/re8ch/flux-federation`.

The initial bootstrap is intentionally inert. After its controllers and
credentials are verified, repositories become active for read-only artifact
fetching while domain Kustomizations stay suspended and `prune` remains false.
Neither phase transfers workload ownership.

## Bootstrap

1. Merge the four domain repository bootstrap pull requests and replace each
   staged lock with the resulting `main` SHA.
2. Merge the service-backend bootstrap after CI passes. Verify four controller
   Deployments in each domain namespace and confirm every registration remains
   suspended.
3. Through the audited control-plane path, run
   `bootstrap-shared-secrets.sh`. It copies the existing SOPS private key and
   OCI reader Secret without placing plaintext in Git.
4. Unsuspend only each domain's `GitRepository` and verify signature, revision
   and artifact fetch. Keep its new Kustomization suspended until cutover.

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
