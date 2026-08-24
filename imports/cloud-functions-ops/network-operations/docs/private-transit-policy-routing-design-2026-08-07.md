# Private transit policy-routing design

Date: 2026-08-07 (Asia/Shanghai)

## Address and traffic classes

| Class | Prefix | Initial policy |
| --- | --- | --- |
| Node identity/control | `192.0.2.20/24` | Existing underlay remains authoritative while path scoring is developed |
| Accelerated service | `192.0.2.65/24` | A1/B1 private-transit, per-flow ECMP when both are healthy |
| A1 transport | `192.0.2.63/24` | WireGuard/BGP transport only |
| B1 transport | `192.0.2.208/24` | WireGuard/BGP transport only |

Each node owns `10.250.0.<node-id>/32` on loopback. FRR may advertise only
these accelerated identities in the private-transit routing domain. Kubernetes
Node InternalIP addresses are not imported or exported by private-transit.

The separation is a static classification boundary, not a permanent decision
that control traffic must use ZeroTier. A later controller may select a
different path for control traffic only after evaluating the stricter control
class policy. It must never change application and control paths as an
inseparable side effect of one Node `/32` route.

## Initial path policy

- A1 and B1 are equal candidates for accelerated service traffic.
- Linux/FRR multipath is per flow; per-packet load balancing is prohibited.
- Loss of either BGP next hop leaves the other installed without changing the
  Node identity route.
- A failed path must remain healthy for a minimum hold period before it is
  restored to the ECMP set.
- Default routes are never exchanged.

## Dynamic controller inputs

The controller will measure handshake age, BFD state, smoothed RTT, jitter,
loss, available throughput, utilization, region, provider failure domain,
cost, and transfer allowance. It will use hysteresis and a minimum hold time
when translating those measurements into path weights.

Control-plane flows require a separate score profile and acceptance gate for
etcd quorum latency, API readiness, and asymmetric-path safety. They can become
dynamically routed, but only independently of accelerated service traffic.

The deployed controller currently combines static preference, configured link
capacity, active RTT, active loss, and interface byte-rate utilization. A1 and
B1 publish only their current utilization and timestamp on TCP 9918 bound to
their ZeroTier addresses; workers reject samples older than ten seconds. It
applies a 20 percent material-change threshold and a 60 second minimum hold
time. Missing telemetry remains unknown rather than being interpreted as an
idle link. Full-duplex utilization uses the busier of RX and TX relative to the
configured per-direction capacity, rather than incorrectly summing both
directions.

## Live canary results

The first node-local controller is active on `amdwsl-zt`. Eight public nodes
own `10.250.0.<node-id>/32` service identities. A1 and B1 both established all
eight private-transit WireGuard/eBGP sessions, while Kubernetes InternalIP
routes remained unchanged.

Initial random-path testing exposed two distinct faults and validated their
fixes:

1. BGP timers alone caused about 7.4 seconds of loss. Enabling BFD reduced the
   single-target failover test from 51% loss to zero loss when the non-selected
   path was removed.
2. Common service identities initially produced asymmetric ECMP return drops.
   A connection mark on each public node now returns a flow through the same
   private-transit interface on which it arrived. After this correction, both
   forced A1 and forced B1 probes succeeded for the same overseas target.

In the eight-target acceptance test, A1 was randomly selected and stopped for
six seconds. Five targets had zero loss; rancher-panel lost 4/70 probes,
artchais lost 3/70, and restricted-node-6dc3f140f9 lost 3/70. All eight identities recovered and the
policy agent remained active. A single short loss sample changed a candidate
weight but did not immediately change the installed weight because the minimum
hold and change threshold were active.

The controller is now active on A1, B1, the three private workers, all seven
public Kubernetes nodes (including overseas-edge-50), and the registered
replica-186 transit peer. Each of
those nodes reported valid policy weights for all eight accelerated service
identities. Public nodes select their own `wg-pt-a1`/`wg-pt-b1` interfaces;
workers select the A1/B1 ZeroTier next hops; A1 and B1 use their local fabric
plus the other gateway as an alternate. This corrected the R640-specific fault
where A1/B1 had incorrectly been treated as on-link Ethernet next hops behind
MikroTik.

Application recovery was exercised from all three private workers against the
Headlamp HTTPS endpoint during an A1-path interruption. They completed 44/46,
43/47, and 42/48 requests respectively and returned HTTP 200 afterward. At the
latest post-rollout check, the public Headlamp root returned 200 in 91 ms and
its unauthenticated cluster health proxy returned 401 in 258 ms rather than the
earlier 500 response. All 12 Kubernetes nodes remained Ready.

In the final source-correct application test, A1 was randomly interrupted for
eight seconds while all three private workers accessed Headlamp through their
own `10.250.0.x` identities. They completed 39/40, 38/40, and 39/40 HTTP 200
requests and recovered without intervention. A subsequent eight-destination
test again randomly selected A1: six accelerated identities had zero loss;
artchais and restricted-node-6dc3f140f9 each lost 10/60 packets during convergence and then recovered.
The A1 WireGuard fabric and policy agent were active after restoration.
