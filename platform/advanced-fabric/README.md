# Advanced Fabric Cloud SSA release

The deployment truth is the independently versioned RE8CH Advanced Fabric OCI
chart registered with Artifact Hub:

`oci://ghcr.io/advanced-fabrics/charts/advanced-fabric`

The accepted OCI artifact is digest pinned and verifies the public Advanced
Fabrics release workflow's keyless Cosign identity. The Cloud SSA bundle
upgrades chart CRDs before applying the release. During DNS shadow observation
the fabric is forced to `observe-only`
and `applyEnabled=false`; DNS promotion is a separate reviewed change.

`upgrade.preserveValues=true` is a one-release adoption safeguard for the
existing manually managed `service-vip-fabric` Helm release. Remove it after all
environment-specific values have been migrated into this directory.
