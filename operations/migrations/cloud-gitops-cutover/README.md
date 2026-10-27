# Construction cutover to cloud GitOps

The construction-phase cutover freezes local Flux before all replacement
contracts are complete. Missing coverage is tracked explicitly by the existing
release cohorts; it is not a reason to restart local Flux. GitHub Actions and
the existing bounded SSA executor remain the delivery path.

## Verified preparation

- Platform snapshot audit: `op_033c87cad5404163bb17c3fd31908fd6`.
- Private snapshot on control-plane host `a1-wsl-zt`:
  `/var/lib/re8ch/cloud-gitops-cutover/20261027T171845Z` (UTC timestamp).
- Snapshot includes 44 Kustomizations, 30 HelmReleases, 22 controller Deployments,
  Git/OCI/Helm sources and 164 Helm release-history Secrets. Files remain mode
  0600 inside a mode 0700 directory. Secret contents are never public artifacts.
- Kubernetes `/readyz` returned `ok`. Public GitHub-hosted OIDC dry-runs already
  succeeded for agent and identity (runs 40537756368 and 40537748396).

## Bounded execution through the Re8ch platform

After this signed PR passes Actions and is merged:

1. Stop the root `flux-system/kustomize-controller`, then the four domain
   kustomize controllers, so no parent can restore local writers.
2. Set the remaining Flux source/Helm/notification/image controller Deployments
   to zero in the same five namespaces. Preserve their pod templates and RBAC.
3. Suspend Flux Kustomization, HelmRelease and source objects; disable
   Kustomization pruning. Preserve all objects, Helm history and CRDs.
4. Verify zero ready Flux controller pods and a healthy Kubernetes API. Check
   PublicEdge, DNS and runtime operators independently; do not scale them down.
5. Bootstrap reviewed cloud executor permissions/state through the platform API,
   then publish/adopt contracts and reconcile accepted immutable digests.

The final root Kustomize patches enforce dormant controllers and suspended
legacy owners after any later root render. No namespaces, workloads, CRDs,
volumes, Helm histories or secrets are removed. Existing Kubernetes operators
continue to maintain workload runtime behavior.

A fallback means restoring a needed resource through the cloud/platform API.
Starting local Flux again requires a separately reviewed user-directed change;
it is not an automatic failure handler.
