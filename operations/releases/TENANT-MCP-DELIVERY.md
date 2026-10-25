# Tenant MCP: source to deployment

## Audited chain (2026-10-25)

Product source: `platform/apps/tenant-mcp` (Go). The actual container build is
the multi-stage Dockerfile in that directory. `tenant-go-release.yml` invokes
the shared BuildKit publisher on Macmini, because the existing public Harbor
upload path from hosted workers is unreliable. The product is not moved here.

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

Merge the service-backend change first, then wait for
`flux-system/tenant-mcp-image` Ready and the ConfigMap to exist. The initial value
matches the observed live image. Only then merge the observability companion PR
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
