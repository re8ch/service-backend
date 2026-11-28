# Advanced Fabric Cloud SSA release

The deployment truth is the independently versioned RE8CH Advanced Fabric OCI
chart registered with Artifact Hub:

`oci://ghcr.io/advanced-fabrics/charts/advanced-fabric`

`cloud-functions` may retain historical migration manifests, but no retired
Flux owner may deploy its vendored chart. The accepted OCI artifact is digest
pinned and verifies
the public Advanced Fabrics release workflow's keyless Cosign identity; the
The Cloud SSA bundle upgrades chart CRDs before applying the release; the
retained `HelmRelease` expresses historical adoption inputs only. During DNS
shadow observation the fabric is forced to `observe-only`
and `applyEnabled=false`; DNS promotion is a separate reviewed change.

`upgrade.preserveValues=true` is a one-release adoption safeguard for the
existing manually managed `service-vip-fabric` Helm release. Remove it after all
environment-specific values have been migrated into this directory.
