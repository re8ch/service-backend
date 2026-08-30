# Advanced Fabric Flux release

The deployment truth is the independently versioned RE8CH Advanced Fabric OCI
chart registered with Artifact Hub:

`oci://ghcr.io/re8ch/charts/re8ch-advanced-fabric`

`cloud-functions` may retain historical migration manifests, but Flux must not
deploy its vendored chart. The `OCIRepository` is digest pinned, and the
`HelmRelease` upgrades chart CRDs with `CreateReplace` before reconciling the
release. During DNS shadow observation the fabric is forced to `observe-only`
and `applyEnabled=false`; routing mutation remains separately guarded.

Advanced Fabric DNS is the promoted cluster resolver at `192.0.2.149`.
`kube-dns-compat.yaml` preserves the standard `192.0.2.240` VIP for Pods created
before the kubelet rollout while selecting the same Advanced Fabric endpoints.
All control-plane nodes disable the packaged K3s CoreDNS addon through the
host-operation configuration under `operations/host-ops/k3s-dns/`.

restricted-node-6dc3f140f9 has a dedicated Advanced Fabric DNS endpoint. Its stable DNS VIP selects
only that local endpoint, avoiding a recovery dependency on cross-node PodCIDR
forwarding. The retired `coredns-r640` Deployment remains scaled to zero.

`upgrade.preserveValues=true` is a one-release adoption safeguard for the
existing manually managed `service-vip-fabric` Helm release. Remove it after all
environment-specific values have been migrated into this directory.
