# RE8CH Network Operations

This directory is the site-specific operations boundary for the RE8CH cluster
network. It intentionally remains separate from the public
[`re8ch/advanced-fabric`](https://github.com/advanced-fabrics/core) product
repository.

## Contents

- `manifests/cilium`: Cilium bootstrap, native-routing rollout, Gateway and
  control-plane HA values.
- `manifests/network`: real PodCIDR, private-transit, public-mesh and RouterOS
  inventories and host services.
- `manifests/harbor`: Harbor HTTPS/OCI L4 TLS passthrough resources. Harbor 443
  is not an HTTPRoute and must not be converted to Envoy TLS termination.
- `manifests/egress-fabric`: egress isolation policy.
- `scripts`: FRR, PodCIDR, policy-routing, RouterOS and economics tooling.
- `docs`: historical decisions, performance checkpoints and runbooks.

## Cilium and Gateway authority

Network runtime desired state is owned by the autonomous
`flux-network-system` stack sourcing `pillars-infra/network-gitops`. This
repository retains the public authority inventory, controller lifecycle,
bounded cluster RBAC, and cross-domain policy boundary. Do not reintroduce a
second Cilium Helm owner under the main Flux.

R640 retains native XDP and uses eBPF masquerading on `eno2np1`. Its node
configuration must explicitly clear the chart-wide legacy
`egress-masquerade-interfaces` value; omission inherits `eth0` and makes the
Cilium agent fail closed. `Gateway Programmed=True` proves only control-plane
acceptance and address allocation, so every public recovery must also probe
TLS/HTTP through `10.251.0.4` and `223.159.210.222`.

See
[`docs/r640-cilium-gateway-recovery-2026-10-19.md`](docs/r640-cilium-gateway-recovery-2026-10-19.md)
for the incident evidence, permanent GitOps changes, verification commands and
rollback boundary.

## Public/private boundary

The public chart contains CRDs, controllers, schemas and a sanitized example.
This directory owns actual node names, IP addresses, PodCIDRs, ASNs, provider
regions, quotas, failure domains and rollout evidence. Credentials remain in
Kubernetes Secrets or node-local protected files and must never be copied here.

## Compatibility

The original `manifests/k3s/...`, `scripts/...` and `docs/...` paths are retained
during the migration because live kustomizations and systemd deployment tools
still reference them. `MIGRATION.yaml` records the new canonical locations.
Remove a legacy path only after `rg` confirms that no live renderer, kustomize
file, runbook or service unit consumes it.

