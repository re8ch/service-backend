# RE8CH distributed egress fabric

This stack augments NetBird rather than replacing it. NetBird remains the
enterprise terminal VPN/access plane and may use routing peers for controlled
CN/overseas fabric entry. Mihomo replaces relay-bound proxy-aware workload paths
only; it does not carry arbitrary terminal IP traffic.

## Boundary

- Cilium, BGP and the existing underlays retain node and Service L3
  reachability. This stack does not advertise routes or change them.
- The former blanket prohibition on TUN, `hostNetwork` and `NET_ADMIN` is
  retired. Dedicated gateway Pods may use TUN and narrowly scoped `NET_ADMIN`
  for high-performance transparent forwarding. Node-local proxy workers remain
  unprivileged where possible; host networking and host-route ownership are
  used only when a measured node-local design requires them.
- Each eligible node gets one `mihomo-node` Pod. The `mihomo-node` Service uses
  `internalTrafficPolicy: Local`, so a workload using
  `http://mihomo-node.egress-fabric.svc:7890` reaches the node-local proxy.
- The initial eligible set is `restricted-node-6dc3f140f9` and `amdwsl-zt`. A1/B1 are held back
  because their Docker Hub pulls time out. Expand the hostname allowlist only
  after the pinned image is mirrored, signed and scanned in Harbor. Nuclio and
  database nodes remain excluded.
- The LA gateway runs on `overseas-edge-50` and exits with that node's ordinary
  network. Its ClusterIP path is carried by Cilium over whichever node underlay
  is currently reachable. Its mixed listener is unauthenticated but reachable
  only from `mihomo-node` Pods under NetworkPolicy; it is never public.
- Stage one carries compiled domain suffixes for the eight canary categories
  already used by the NetBird GeoDB job. Runtime GeoSite/GeoIP downloads are
  intentionally absent: GitHub downloads exceeded Mihomo's deadline on a
  domestic node. The follow-up synchronizer must compile maintained source data
  once, update the shared config, and reload instances through their API.
- LA public-direct and Kuala Lumpur direct/relay paths are injected through the
  `egress-fabric-paths` Secret. Missing paths are absent, not fake healthy
  endpoints. Never commit passwords or provider subscription payloads.

## Path policy

Mihomo's native provider health check and `automatic-fallback` group provide a
local safety net. The lightweight controller additionally polls each DaemonSet
instance through its private controller API, calculates:

```text
score = EWMA(latency_ms)
      + 1500 * observed_loss_ratio
      + utilization_penalty
      + configured/measured_throughput_penalty
      + monthly_quota_penalty
      + relay_hop_penalty
```

and updates the `stable-egress` selector only after a 15% improvement and a
60-second minimum hold. A failure scores as the probe timeout. `DIRECT`, `P2P`
and `RELAY` are candidates in the same model; direct avoids the relay-hop
penalty but is not forced. Configured bandwidth is capped by measured
throughput. Low-bandwidth CN nodes remain bounded fallback candidates, and an
unhealthy or missing node removes only itself rather than gating unrelated
route updates.

The same Controller also exposes a namespace-local route decision/telemetry API
on `path-controller:8080`. NetBird peer observations include RTT/loss EWMA,
P2P/relay mode, utilization, region, health and optional cost. Route decisions
are shadow-only by default; setting two exact `CANARY_CLIENT_IDS` does not itself
mutate NetBird. A version-specific write adapter must only be enabled after the
live Networks/exit-route inventory and service-user permissions are verified.

## External path Secret

`bootstrap-secrets.sh` creates an empty provider safely. Replace it with paths
that have actually been installed and tested. Example structure only:

```yaml
proxies:
  - name: LA-PUBLIC-DIRECT
    type: ss
    server: <LA public endpoint>
    port: 30443
    cipher: 2022-blake3-aes-128-gcm
    password: <secret>
  - name: KL-DIRECT
    type: hysteria2
    server: <KL endpoint>:8443
    password: <secret>
    sni: <certificate name>
  - name: KL-RELAY
    type: hysteria2
    server: <relay endpoint>:8443
    password: <secret>
    sni: <certificate name>
```

Apply it without displaying values:

```bash
sudo k3s kubectl -n egress-fabric create secret generic egress-fabric-paths \
  --from-file=paths.yaml=/secure/path/paths.yaml \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
sudo k3s kubectl -n egress-fabric rollout restart ds/mihomo-node
```

## Staged deployment

Run from restricted-node-6dc3f140f9 while `/readyz` is `ok` and the exact target nodes are healthy.
An unrelated worker being `NotReady` is an alert, not an apply gate. Mihomo may
be installed on every eligible non-Nuclio/non-Qianwen node as a routing
capability, while the controller admits only healthy candidates and requires an
N-of-M path quorum; no individual low-bandwidth CN candidate is mandatory.

```bash
cd /path/to/cloud-functions/manifests/k3s/egress-fabric
KUBECTL='sudo k3s kubectl' ./bootstrap-secrets.sh
sudo k3s kubectl apply --server-side --field-manager=re8ch-egress-fabric -k .
KUBECTL='sudo k3s kubectl' ./verify.sh
```

Server-side apply is required because the compressed full GeoDB is about
500 KiB; client-side apply would duplicate it into the last-applied annotation
and exceed Kubernetes' 256 KiB annotation limit.

Canary one workload by adding these environment variables; no namespace-wide
transparent interception is authorized:

```yaml
- {name: HTTP_PROXY, value: http://mihomo-node.egress-fabric.svc:7890}
- {name: HTTPS_PROXY, value: http://mihomo-node.egress-fabric.svc:7890}
- {name: NO_PROXY, value: .svc,.cluster.local,192.0.2.203/8,192.0.2.225/10}
```

Validate DNS, target HTTPS, observed public IP, controller selection and
failover before adding another workload. External users should consume an
authenticated Mihomo client profile containing the same tested paths; the
cluster-local mixed proxy and controller API must never be published.

## Migration and rollback

1. Deploy with only `LA-FABRIC`, then canary explicit proxy traffic.
2. Add authenticated LA public and KL paths one at a time.
3. Run failure tests and confirm controller hold/hysteresis behavior.
4. Validate one CN and one overseas NetBird client against all four quadrants.
5. Expand terminal membership gradually while retaining the production routes.
6. Remove only proven-redundant legacy route objects; retain NetBird as the
   terminal access plane.

Rollback removes proxy environment variables first. Scaling `mihomo-node` and
the controller to zero then returns traffic to its original direct behavior.
Because no host route is touched, rollback does not require Cilium/BGP repair.

## restricted-node-6dc3f140f9 edge authority

restricted-node-6dc3f140f9 does not directly own the physical ZeroTier ingress. MikroTik owns
`192.0.2.183` and proxies traffic to the restricted-node-6dc3f140f9 LAN address
`192.0.2.97`. Services intended to use that ingress must listen on
`192.0.2.97` or `0.0.0.0`; binding only the restricted-node-6dc3f140f9 dummy
`192.0.2.183/32` bypasses the MikroTik DNAT destination.

As of 2026-08-09, existing specific DNAT rules win first. MikroTik preserves
its active WAN UDP control ports (IKE/IPsec, ZeroTier and deployed WireGuard
listeners) plus ESP. All remaining public TCP, UDP and other IP protocols for
`192.0.2.79` default-DNAT to `192.0.2.97` without port translation.
The final WAN masquerade provides symmetric restricted-node-6dc3f140f9 egress. restricted-node-6dc3f140f9 UFW permits
traffic arriving on `eno2np1` for `192.0.2.97`; service authentication and
host hardening are therefore mandatory. Add a per-port MikroTik rule only for
translation, source restriction, or traffic retained by RouterOS.
