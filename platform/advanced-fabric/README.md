# Advanced Fabric Flux release

The deployment truth is the independently versioned RE8CH Advanced Fabric OCI
chart registered with Artifact Hub:

`oci://ghcr.io/re8ch/charts/re8ch-advanced-fabric`

`cloud-functions` may retain historical migration manifests, but Flux must not
deploy its vendored chart. The `OCIRepository` is digest pinned, and the
`HelmRelease` upgrades chart CRDs with `CreateReplace` before reconciling the
release. During DNS shadow observation the fabric is forced to `observe-only`
and `applyEnabled=false`; DNS promotion is a separate reviewed change.

`upgrade.preserveValues=true` is a one-release adoption safeguard for the
existing manually managed `service-vip-fabric` Helm release. Remove it after all
environment-specific values have been migrated into this directory.
