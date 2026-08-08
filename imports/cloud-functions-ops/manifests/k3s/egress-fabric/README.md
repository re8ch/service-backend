# RE8CH distributed egress fabric

This stack replaces NetBird **route peering** as the cross-region data plane.
NetBird may remain an enrollment/control service, but no customer or workload
route should depend on its routing peer after migration.

## Boundary

- Cilium, BGP and the existing underlays retain node and Service L3
  reachability. This stack does not advertise routes or change them.
- Mihomo runs without TUN, `hostNetwork`, `NET_ADMIN`, privileged containers,
  host mounts or host ports. It is an explicit HTTP/SOCKS proxy only.
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
score = EWMA(latency_ms) + 1500 * observed_loss_ratio
```

and updates the `stable-egress` selector only after a 15% improvement and a
60-second minimum hold. A failure scores as the probe timeout. These defaults
avoid oscillation; change them through controller environment variables only
after observing real links.

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

Run from restricted-node-6dc3f140f9 only while all nodes are Ready and `/readyz` is `ok`:

```bash
cd /path/to/cloud-functions/manifests/k3s/egress-fabric
KUBECTL='sudo k3s kubectl' ./bootstrap-secrets.sh
sudo k3s kubectl apply -k .
KUBECTL='sudo k3s kubectl' ./verify.sh
```

Canary one workload by adding these environment variables; no namespace-wide
transparent interception is authorized:

```yaml
- {name: HTTP_PROXY, value: http://mihomo-node.egress-fabric.svc:7890}
- {name: HTTPS_PROXY, value: http://mihomo-node.egress-fabric.svc:7890}
- {name: NO_PROXY, value: .svc,.cluster.local,192.0.2.183/8,192.0.2.81/10}
```

Validate DNS, target HTTPS, observed public IP, controller selection and
failover before adding another workload. External users should consume an
authenticated Mihomo client profile containing the same tested paths; the
cluster-local mixed proxy and controller API must never be published.

## Migration and rollback

1. Deploy with only `LA-FABRIC`, then canary explicit proxy traffic.
2. Add authenticated LA public and KL paths one at a time.
3. Run failure tests and confirm controller hold/hysteresis behavior.
4. Move selected workloads and external client profiles from NetBird routes.
5. Remove GeoDB Networks and scale `netbird-router-la` to zero only after all
   consumers have migrated. Keep NetBird identity/control services if desired.

Rollback removes proxy environment variables first. Scaling `mihomo-node` and
the controller to zero then returns traffic to its original direct behavior.
Because no host route is touched, rollback does not require Cilium/BGP repair.
