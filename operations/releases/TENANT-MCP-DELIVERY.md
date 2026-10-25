# Tenant MCP: source to deployment

## Audited chain (2026-10-25)

Product source: `platform/apps/tenant-mcp` (Go). The actual container build is
the multi-stage Dockerfile in that directory. `tenant-go-release.yml` invokes
the shared BuildKit publisher on the governed R640 builder (migrated on main
before activation). The product is not moved here.

Before this change, main compiled the image in validation, then compiled it
again for publication. BuildKit produced an SBOM and finalize scanned it again.
`promote_tenant_mcp.py` then updated three legacy files, after which
`domain-oci-release.yml` could package the domain and propose another digest PR.

Read-only cluster inspection found the legacy HelmRelease in `flux-system`,
`domain-platform-birfucate-metering` and `reconcile-observability-metering`
suspended. The active owner is `HelmRelease/birfucate-metering` in
`flux-observability-system`, declared in
`siren-observer/observability-gitops:clusters/re8ch/resources.yaml`.
Consequently, the old promotion was not a working path to that active owner.
The observed image was `sha256:dbd60536580fdbed4915871aee6ee81806c18f9bce1aa8f80d8e7479d85dff60`.
Its HelmRelease was reconciling at inspection time; this audit is not a health certificate.
During implementation, observability main advanced to `ed719549` with image
`sha256:00ce4a96b6077a878f5358099ff1be41f3b7701307ea303245c7655b33b6b5fb`.
At activation, observability main `020cf862` and the Ready live HelmRelease both
use `sha256:ee3cb1bc01d4db03e109ffaa2cdf8945daeab73a537f00ad4f4847df19319857`.
The handoff is seeded from this latest state to avoid rolling it back.

## Replacement

1. Source PR: Go tests, vet, differential checks and Dockerfile validation.
2. Main: tests, one BuildKit image publication with native provenance and SBOM.
3. Finalize: sign and verify that exact digest; no rebuild or second SBOM scan.
4. Existing domain-promoter App: maintained `create-pull-request` action with
   GitHub commit signing updates **only** `clusters/re8ch/tenant-mcp-image/image.yaml`.
   A stable branch bounds outstanding update PRs. A source/build-definition diff
   rejects old reruns after newer source reaches main. Repository rules and
   required checks decide admission through native auto-merge.
5. The existing signed GitRepository and a small Kustomization sync that
   ConfigMap into `flux-observability-system`. Its Flux watch label triggers
   the active HelmRelease's required `valuesFrom` reference.
6. Helm reconciles the existing chart with the new image value. Kubernetes
   rollout/readiness remains runtime work. No domain OCI publication, second
   promotion PR, new Python planner, external webhook service or App grant.

Image-only commits do not match the image build or domain publication triggers.
Tenant source and the retired metering module/chart are excluded from automatic
domain publication. Other active modules retain their existing release paths.
Legacy declarations and digest history remain frozen for historical inspection.
PR validation still builds a test image; it is deliberately separate from trusted
main publication. This change removes the duplicate **main** build, not PR checks.

## Activation and rollback

Publication and cosign use the dedicated Harbor project robot
`robot$image+github-tenant-mcp`, with repository pull/push in the private `image`
project. Harbor project robots cannot narrow this grant to a single repository;
no project administration, deletion, tenant access or user management is granted.
The sole CI credential copy is held in GitHub's `production` environment as
`TENANT_MCP_REGISTRY_USERNAME` and `TENANT_MCP_REGISTRY_PASSWORD`. The workflow
does not use the shared `HARBOR_*` admin secrets or a tenant's `re8ch` robot.

The robot is provisioned for 90 days. Rotate this dedicated robot before expiry
through the platform administration path and replace these two GitHub secrets;
do not rotate the Harbor administrator or copy its password into CI. GitHub's
environment public key seals the values inside the platform boundary before
transfer; neither plaintext nor a second Kubernetes credential copy is needed.
Existing workflows using shared credentials are outside this product's cutover.

Merge the service-backend change first, then wait for
`flux-system/tenant-mcp-image` Ready and the ConfigMap to exist. The initial value
matches the latest reviewed consumer image. Recheck that it still matches the
consumer's current main before activation. Only then merge the observability companion PR
that removes its inline tenant API image and adds a required ConfigMap reference.
A missing reference fails reconciliation instead of silently selecting a default.
Check the active HelmRelease and tenant MCP Deployment image, rollout and readiness
after a reviewed image PR; successful CI alone does not establish deployment.

Rollback an image by a signed PR reverting this single digest. Do not rebuild it.
To roll back the handoff itself, restore an explicit known-good image in the active
observability HelmRelease and remove `valuesFrom` in one PR before removing the
ConfigMap. `prune: false` prevents removal of the handoff from deleting its value.

## GitHub features used deliberately

GitHub App installation tokens, GitHub-signed bot commits, PR checks/rulesets,
native auto-merge, workflow path filters, concurrency and same-run artifact
handoff replace custom promotion API/GraphQL code. Existing GitHub-to-Flux source
notifications remain usable; interval reconciliation supplies convergence.
Custom Properties are repository classification, not mutable image locks.
Codespaces is a development environment, not a deployment controller. Neither is
added to the production path merely to increase the number of GitHub features.

References: [signed App PRs](https://github.com/peter-evans/create-pull-request/blob/v7/docs/concepts-guidelines.md#commit-signature-verification-for-bots),
[workflow path filters](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow),
[Flux values references](https://fluxcd.io/flux/components/helm/helmreleases/#values-references).
