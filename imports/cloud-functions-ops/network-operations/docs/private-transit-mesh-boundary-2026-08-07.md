# Private transit mesh boundary

Date: 2026-08-07 (Asia/Shanghai)

`wg-public-mesh` contains nodes with a truthful, externally authoritative
public path. Nodes without such a path belong to a separate future
`wg-private-transit` routing domain.

The private domain should use A1 and B1 as dual gateways and include private
workers such as `amdwsl-zt`. Private workers peer with A1/B1 over the existing
ZeroTier underlay; they must not build a WireGuard tunnel whose endpoint and
advertised Node `/32` are the same address, because that creates recursive
routing. A1/B1 may establish outbound tunnels to selected
public gateways, but they must not be represented as publicMesh endpoints or
given Node ExternalIP values. FRR on the boundary gateways exchanges only
explicit public-node `/32` routes and applies region/local-preference policy;
ZeroTier remains the fallback and Kubernetes InternalIP authority.

Live activation is gated on confirming inbound policy on the selected public
gateways. A follow-up audit corrected an earlier false conclusion: the three
nodes reached the Qwen HTTPS endpoint normally; only `api.ipify.org` was
unreachable. A1 and B1 currently share SNAT identity `192.0.2.55`, while
`amdwsl-zt` uses `192.0.2.111`. No host-side NAT repair is required. The
shared A1/B1 SNAT is, however, one upstream failure domain even though A1 and
B1 remain separate compute/reboot domains.

## Stage 1 activation and rollback record

Stage 1 was activated on 2026-08-07 with two independent fabrics:

- `wg-pt-a1`: `192.0.2.160/24`, UDP `51889`, gateway `restricted-node-1eb0570f74`;
- `wg-pt-b1`: `192.0.2.90/24`, UDP `51890`, gateway `restricted-node-296f36f2fb`.

Both gateways established WireGuard and eBGP sessions with all eight public
nodes after the cloud security rule for `pg-re8ch-replica-186` was added.
`amdwsl-zt` established eBGP with both gateways over ZeroTier and does not run
either private-transit WireGuard interface. FRR import/export prefix lists
permit node `/32` routes only. No default route is accepted or advertised, and
BGP multipath remains disabled for stage 1.

The BGP routing portion was withdrawn later the same day. Advertising the
Kubernetes Node `InternalIP` `/32` values changed the path used by embedded-etcd,
kube-apiserver, kubelet, and Cilium traffic between the three control-plane
nodes. During the test, etcd linearizable reads rose from a 100 ms warning
threshold to roughly 2.2--5.3 seconds; API requests then returned 429/500 and
reported `storage is (re)initializing`. Stopping FRR on A1, B1, and
`amdwsl-zt` restored the original ZeroTier control path and Kubernetes
`readyz` passed again. FRR was disabled at boot on those three nodes. The
WireGuard interfaces, keys, firewall rules, and public-node FRR neighbor
definitions remain available for a redesigned test.

The next activation must not advertise `10.181.22.x` Node InternalIP routes.
Use a separate service/transit address family or policy-routing table so that
etcd ports 2379/2380, K3s 6443/9345, kubelet 10250, and Cilium control/data
traffic retain their established underlay independently of accelerated
application flows.

restricted-node-6dc3f140f9 is behind the MikroTik edge. UDP `51889` and `51890` use fixed DNAT from
`192.0.2.79` to `192.0.2.97` on the same ports. The DNAT rules accept
only sources in `re8ch-private-transit-gateways`, currently
`192.0.2.55`. Existing WAN masquerade and connection tracking provide the
symmetric reply mapping. Do not add an unrestricted endpoint-independent NAT
rule: it is unnecessary for WireGuard and would weaken the source restriction.

## Adaptive path selection

Each node and path should carry two classes of attributes:

- static: region, city, provider, failure domain, link capacity, monthly
  transfer allowance, metering cost, and gateway role;
- dynamic: smoothed RTT, jitter, loss, available throughput, utilization,
  handshake age, BFD state, and recent path stability.

BFD is a failure detector, not a latency optimizer. A small routing-policy
controller should translate the attributes into BGP local-pref/MED/community
changes and weighted Linux nexthops. Selection must be per-flow with resilient
hashing; per-packet ECMP is prohibited because it reorders TCP, QUIC, and
Cilium encapsulated traffic.

A1 and B1 should maintain independent outbound WireGuard sessions and may
prefer different regional public gateways. Private workers learn both gateway
next hops. Equal healthy scores produce weighted ECMP; materially different
scores select a preferred path while retaining the other as active capacity,
not merely a failure-only backup. Hysteresis and minimum hold times prevent
route flapping. Stage 1 deliberately leaves BGP multipath disabled; static
weights and ECMP belong to the later adaptive-routing phase.
