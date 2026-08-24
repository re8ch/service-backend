# RE8CH Advanced Fabric

Chart `0.3.x` upgrades the existing `re8ch-network-fabric` Helm release in
place. It adds the seven-spine A/B topology, non-transit C leaves, native-route
rollout configuration, observe-first economics controller, host validation
agent, and `TrafficPolicy.networking.re8ch.com/v1alpha1`. It does not create a
second routing authority.

The default is deliberately safe:

```yaml
advancedFabric:
  mode: observe-only
  applyEnabled: false
  emergencyDisable: false
  weightedEcmp: {enabled: false}
```

In this mode the controller resolves workload selectors, writes status and a
desired-state ConfigMap, while the host agent only verifies that FRR is active.
It cannot modify FRR, nftables, `ip rule`, or kernel routes. Guarded apply must
remain disabled until the seven-day observation gate and live failure tests
have passed.

Example workload policy:

```yaml
apiVersion: networking.re8ch.com/v1alpha1
kind: TrafficPolicy
metadata: {name: batch-greedy, namespace: example}
spec:
  podSelector: {matchLabels: {workload-class: batch}}
  destination: {class: public-internet}
  profile: greedy
  fallbackProfile: balanced
  priority: 100
```

The economics plane may change only LOCAL_PREF, policy-table ECMP eligibility
and validated weights. ASN, peer membership, prefix ownership, redistribution,
default-route origination and FRR daemon topology remain immutable network
truth.

This directory defines the safe boundary between Cilium LB IPAM and the
existing FRR transit fabrics. Stage 1 is live with an opt-in canary; production
Services remain excluded unless they carry both advertisement labels.

It remains compatible with the existing `re8ch-network-fabric` Helm release.
The canonical public OCI endpoint uses Harbor HTTPS on port 443; do not append
the retired `:28443` port. Releases are packaged for:

```sh
helm upgrade --install re8ch-network-fabric \
  oci://registry.re8ch.com/helm/re8ch-advanced-fabric \
  --version 0.3.0 --namespace egress-fabric --create-namespace
```

Artifact Hub publication and repository-verification steps are documented in
`ARTIFACT_HUB.md`. The chart does not include registry credentials or the
retired Xuanyuan Docker Hub proxy. Cluster image mirrors must target
`https://registry.re8ch.com` and are managed separately from workload routing.

The default install does not enable Mihomo. Set `mihomoGateway.enabled=true`
only after creating `mihomoGateway.existingPathsSecret`; provider credentials
and encrypted transport definitions are never included in the chart.

## Address and routing authority

| Domain | Prefix | Authority | May be optimized by FRR |
| --- | --- | --- | --- |
| Kubernetes node identity | `192.0.2.253/24` | K3S Node InternalIP / ZeroTier | No |
| A1/B1 and private-transit identity | `192.0.2.106/24` | Host loopback + FRR | Yes, service/transit use only |
| Kubernetes Service VIP | `192.0.2.215/24` | Cilium LB IPAM | Yes |
| Pod CIDR | `192.0.2.224/16` | Cilium/Kubernetes IPAM | No |

The former incident happened when routing policy changed the next hop for the
Node InternalIP domain. The service-VIP design avoids that failure mode by
advertising only exact `/32` LoadBalancer VIPs from `192.0.2.215/24`.

## Intended data path

```text
selected LoadBalancer Service
  -> Cilium allocates one 10.251.0.x VIP
  -> A1/B1 advertise that /32 to the ICP FRR handoff
  -> restricted-node-6dc3f140f9 advertises it to both A1 and B1 over private-transit
  -> FRR exports only 192.0.2.215/24 more-specifics into the service fabric
  -> A1/B1 use the high-speed WSL LAN fabric; restricted-node-6dc3f140f9 uses the MikroTik edge
```

Cilium BGP is an advertisement control plane; it does not create the data path.
The receiving FRR next hop must already reach the announcing node through a
validated service/transit address. Never use a learned Node InternalIP route as
that next hop.

## Stage 0 (repository-only, no traffic change)

- `address-domains.json` records the disjoint routing authorities.
- `lb-ipam-disabled.yaml` creates no allocation because `disabled: true`.
- `service-advertisement.yaml` selects only Services carrying both
  `networking.re8ch.com/lb-pool=service-vip` and
  `networking.re8ch.com/bgp-advertise=enabled`.
- `frr-service-vip-policy.conf` is a neighbor-free policy fragment which
  permits only exact Service VIP `/32` routes and rejects protected domains.
- `validate.py` rejects an enabled pool, overlapping authority prefixes,
  ClusterIP/ExternalIP/PodCIDR advertisements, or an overly broad selector.

Creating these resources is not acceptance to apply them. Stage 0 must first
pass the live read-only checks listed below.

## Required live gates before a change

1. All three embedded-etcd endpoints are healthy and their commit/index lag is
   bounded.
2. A1/B1 sustained bidirectional transfer still uses `wg-wsl-lan`; small ping
   success alone is insufficient.
3. `ip route get` for every other control-plane InternalIP has the documented
   stable path. No FRR RIB/FIB entry may cover `192.0.2.253/24` or
   `192.0.2.224/16`.
4. The three embedded-etcd control-plane nodes and their Cilium agents are
   Ready. Unrelated worker readiness is not a routing-change gate.
5. FRR import/export policies on A1, B1 and restricted-node-6dc3f140f9/MikroTik accept only exact
   `192.0.2.215/24` service `/32` routes from the Cilium handoff.
6. The selected canary Service has at least two topology-spread Ready endpoints
   and a PodDisruptionBudget.
7. Existing product endpoints remain unchanged; this fabric does not modify
   Qwen workloads or public DNS.

## Stage 1 canary

After the gates pass, enable the pool in a reviewed copy and label exactly one
non-critical LoadBalancer Service. Use `externalTrafficPolicy: Local` only when
the Service's real EndpointSlices correctly identify local Ready endpoints.
With `Local`, Cilium withdraws the VIP from a node that has no local endpoint;
with `Cluster`, every selected BGP node may advertise and an additional
cross-node hop is possible.

Do not aggregate the VIPs. Exact `/32` advertisements make withdrawal and
black-hole analysis deterministic.

## Mihomo gateway Service VIP

When enabled, the optional Mihomo DaemonSet runs only on nodes carrying its
capability label. Its Service defaults to `ClusterIP`. A reviewed deployment
may set `service.type=LoadBalancer`, `service.advertise=true`, and request an
address such as `192.0.2.235`. Cilium then owns that Service address and BGP
announces the exact `/32` from nodes with eligible endpoints.

The VIP solves stable discovery and next-hop failover for proxy clients; it
does not accelerate traffic by itself. The `192.0.2.235` experiment was removed
because BGP selected restricted-node-6dc3f140f9 correctly while remote host-to-Cilium-Service ingress
failed. Keeping the advertisement would have created a deterministic black
hole. Re-enable it only after an external host can complete TCP and HTTP CONNECT
tests through the VIP and withdrawal is verified when the endpoint disappears.

Ceph replication must not use this HTTP/SOCKS gateway. Ceph consumes the
private-transit reachability supplied by the network fabric and uses Ceph
Messenger v2 encryption for storage traffic; Mihomo is for selected public
destinations such as OCI registries.

## Rollback

Remove the two opt-in Service labels first. Confirm the VIP `/32` is withdrawn
from every FRR peer, then disable the pool. Do not delete or change ZeroTier,
`wg-wsl-lan`, K3S Node addresses, Cilium PodCIDRs, public DNS, or the existing
public-mesh/private-transit fallback while rolling back a canary.
