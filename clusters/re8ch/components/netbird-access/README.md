# RE8CH NetBird access service

This directory deploys a single-account NetBird Community control plane at
`access.re8ch.com:8443` plus two container-isolated routing peers. The peers run in
Pod network namespaces on `restricted-node-6dc3f140f9` and `overseas-edge-50`; they do not use
`hostNetwork` and must not change host routes, Cilium, FRR, or Kubernetes node
identity addresses.

## Hard gates

Do not apply the shared control-plane stack while K3s `/readyz` reports an etcd
failure. Run `preflight.sh` through restricted-node-6dc3f140f9 first; its whole-cluster readiness gate
applies to shared control-plane changes, not to an isolated regional relay. The stack also requires the
default `local-path` StorageClass and the existing wildcard TLS Secret in the
`llm-gateway` namespace. The single-replica server and its local volume are
pinned to restricted-node-6dc3f140f9; back up the PVC before moving the control plane.

The current public origin is `192.0.2.138`. The DNS controller owns the A
record. MikroTik must forward public UDP 3478 to restricted-node-6dc3f140f9 UDP NodePort 30478; this
is an edge-router change, not a Kubernetes node route change. Do not proxy this
hostname through Cloudflare because NetBird needs gRPC, WebSocket, relay, and
STUN behavior end to end.

### Regional relay firewall and DNS baseline

The four user-controlled Tencent Cloud small servers and two user-controlled
Alibaba Cloud small servers already allow:

- UDP `3478` for STUN;
- TCP `30000-32767` for relay/NodePort listeners;
- UDP `30000-32767` for UDP relay/NodePort listeners.

Treat this as a standing firewall authorization; do not ask the user to reopen
the same ranges for each relay. It does not replace live validation: check the
selected server identity, port ownership, listener readiness, provider NAT and
external TCP/UDP reachability before adding the endpoint to NetBird management.

All public relay hostnames are published by the K3s ExternalDNS/edge DNS
controllers. Declare the desired hostname and origin on the appropriate
Service/Ingress/Gateway object and let the controller converge it. Do not make
manual `re8ch.com` DNS changes.

## Deployment

From restricted-node-6dc3f140f9:

```bash
cd /path/to/cloud-functions/manifests/k3s/netbird-access
KUBECTL='sudo k3s kubectl' ./deploy-control-plane.sh
```

Create the first owner in the dashboard. Create a setup key for LA (and a CN
key only when the dormant CN peer is deliberately enabled), then:

```bash
export NETBIRD_LA_SETUP_KEY='...'
KUBECTL='sudo k3s kubectl' ./install-routing-peer-secrets.sh
unset NETBIRD_LA_SETUP_KEY
KUBECTL='sudo k3s kubectl' ./deploy-routing-peers.sh
```

Regional external relay canaries use a scoped gate: K3s API/etcd, the exact
target nodes, NetBird public root and a Qwen regional smoke must be healthy.
An unrelated NotReady node that is neither selected nor changed is reported but
does not block adding a relay in another failure domain. Install the shared
relay auth Secret without printing it, then deploy:

```bash
KUBECTL='sudo k3s kubectl' ./install-external-relay-auth.sh
KUBECTL='sudo k3s kubectl' ./deploy-external-relays-canary.sh
```

Relay acceptance is intentionally N-of-M rather than All-of-M. Guangzhou is a
hard rollout target; low-bandwidth or intermittently connected CN candidates
such as Hangzhou are opportunistic. `activate-external-relays.sh` defaults to a
minimum of one available Deployment, while NetBird clients probe all advertised
endpoints and skip unavailable ones. `verify-relay-pool.py` independently checks
public TLS and STUN and succeeds when `MIN_HEALTHY_RELAYS` candidates pass.
Individual CN candidates are therefore allowed to flap without blocking the
control plane, GeoDB publication, or a healthy relay in another failure domain.

The live Cilium installation has kube-proxy replacement, NodePort and HostPort
disabled. These two external-relay Pods therefore use a narrow `hostNetwork`
exception and bind only `31080/TCP+UDP` plus their declared STUN UDP port; they remain non-root,
drop all capabilities, do not receive `NET_ADMIN`, and do not alter host routes.
This exception does not apply to NetBird routing peers or Mihomo.

Canary state on 2026-08-09:

- `relay-gz-1.re8ch.com` -> `rancher-panel` / `192.0.2.192`: DNS, public TLS
  relay on TCP 31080, QUIC relay on UDP 31080 and STUN on UDP 31081 passed.
- `relay-hz-1.re8ch.com` -> `harbor-registry` / `192.0.2.185`: public TLS
  relay on TCP 31080 and STUN on UDP 3478 passed. The target node briefly
  became NotReady during rollout, then recovered and the Deployment converged
  to 1/1 Ready with zero restarts.
- `qianwen-nuclio-79` is explicitly excluded from this rollout.

The management server now advertises both regional relays and both working
STUN endpoints. The former embedded restricted-node-6dc3f140f9 relay is disabled; its exact server
Secret is retained as `netbird-server-config-pre-external-relay-20260809` for
rollback. `activate-external-relays.sh` is idempotent and never overwrites that
first rollback copy.

### Device performance acceptance (2026-08-09)

The real Android peer `yuechu` was tested from the LA routing peer before and
after cutover using the same 20-packet probe:

- embedded restricted-node-6dc3f140f9 relay: 193.2/1236.0/5922.0 ms min/avg/max, 0% loss;
- Guangzhou QUIC relay: 192.2/203.2/218.8 ms, 0% loss;
- Hangzhou relay after forced Guangzhou failure: 184.9/221.8/789.4 ms, 0%
  loss, including the first post-switch packet;
- Hangzhou 60-packet steady sample: 195.2 ms average, 193.2 ms P50,
  215.6 ms P95, 272.7 ms maximum, 14.9 ms standard deviation and 0% loss.

Removing Guangzhou caused the peer to reconnect through Hangzhou in roughly
5-10 seconds; Guangzhou was then restored to 1/1 Ready. The LA `wt0` MTU is
enforced at 1200 after probes found a mobile-relay black hole above an inner
packet size of about 1248 bytes. A 20-packet 1200-byte inner-packet acceptance
test passed with 0% loss and 190.0 ms average RTT.

External relay Pods tolerate `node.kubernetes.io/not-ready` and
`node.kubernetes.io/unreachable` without eviction. This preserves a functioning
public relay when only its K3s/ZeroTier control link is unstable; public TLS,
STUN and real Device traffic remain the authoritative health signals.

## NetBird policy configuration

Use the current **Networks** model for `re8ch-private`, internal domains, and
GeoDB-selected overseas domains. Keep CIDR and domain resources in separate
Networks. The live canary is `GeoDB Overseas via LA`: routing-peer group
`geo-egress-la`, source group `geo-split-clients`, with `google.com` and
`*.google.com` as the first compiled resources. Matching and route selection
happen on the client; the control plane distributes rule changes and does not
make a per-request routing decision. Use a default exit route only for an
explicit global-exit group.

Require clients >= 0.59.2 and routing peers >= 0.59.10. The pinned deployment
currently uses 0.74.6. The `usage-meter` sidecars export aggregate `wt0` and
`eth0` byte counters; these are capacity signals, not billable per-user facts.

## GeoDB synchronization

`build-geodb-snapshot.sh` checks out an explicitly pinned
`MetaCubeX/meta-rules-dat` revision outside Kubernetes and produces one
deterministic gzip snapshot with source and artifact hashes. The daily
`netbird-geodb-sync` Job reads only that mounted snapshot; cluster nodes no
longer fetch GitHub at startup. It reduces the overseas canary profile to
registrable-domain wildcards plus a small root-domain allowlist, then upserts
only `geodb:*` resources into `GeoDB Overseas via LA`. Pruning is disabled by
default, so a failed publication cannot erase the last working rules. The API
token belongs to the `geodb-sync` Network Admin service user and exists only in
the `netbird-geodb-sync` Secret.

The same immutable snapshot is rendered locally for the restricted-node-6dc3f140f9 transparent
gateway. `geosite-cn` contains suffix rules and `geoip-cn` contains collapsed
IPv4/IPv6 CIDRs. The gateway never downloads GeoSite/GeoIP at startup. NetBird
management, overlay and RFC1918 destinations are evaluated before customer
egress rules, so changing an internet exit cannot remove access to development
peers.

## Stable regional peer onboarding

Use one Deployment, setup key, state directory and routing-peer group per
region. `onboard-regional-peer.sh` is create-only: it refuses to mutate an
existing Deployment and refuses a NotReady target node. A failed new peer is
therefore isolated and cannot roll or reconfigure LA, restricted-node-6dc3f140f9, the NetBird server,
or another region. Setup keys auto-assign only that region's router group;
client profile groups are attached later, after reachability and throughput
acceptance.

Keep `NB_HOSTNAME` as a stable ASCII automation identifier. NetBird 0.74.6
supports a separate Unicode peer name in the Dashboard and encodes its DNS
label as Punycode. The current display names are `娄底出口` and `洛杉矶出口`;
renaming them does not rebuild their Kubernetes Pods or change their overlay
IPs.

## Loudi CN / LA overseas split

Mihomo consumes the full pinned CN snapshot. CN suffix/CIDR matches select
`LOUDI-DIRECT`; everything else selects `stable-egress`. `LOUDI-DIRECT` is a
Mihomo-native WireGuard outbound to MikroTik over the LAN only:

- endpoint `192.0.2.240:51891`, transit `192.0.2.253/30`, MTU 1380;
- the private key is stored only in `netbird-mihomo-loudi-wireguard` and the
  rendered provider only in `netbird-mihomo-loudi-path`;
- MikroTik accepts the endpoint only from restricted-node-6dc3f140f9 `192.0.2.169`, forwards the
  tunnel only to `WAN`, NATs it through the main Loudi WAN, and clamps TCP MSS
  to 1200 in both directions;
- no public DNAT, Cilium/BGP mutation, host route, or NetBird control-plane
  change is involved.

This source identity prevents MikroTik's legacy restricted-node-6dc3f140f9 destination-IP PBR from
classifying a second time after Mihomo has already classified a domain. The
NetBird user profile is `loudi-split-clients`, exposed by Network
`娄底出口+海外请求分流`: all IPv4 enters the restricted-node-6dc3f140f9 classifier, CN exits as
`192.0.2.138`, and non-CN exits through LA as `192.0.2.189`. New members
are never auto-added; assign the group only after client-specific acceptance.

RouterOS rollback is based on backup/export
`pre-mihomo-loudi-wg-20260810`. Remove only objects whose comments start with
`RE8CH Mihomo WG`, the `wg-mihomo-loudi` interface/address/peer, then change the
two CN `RULE-SET` targets back to `DIRECT`. Remove the NetBird policy/Network
only after `loudi-split-clients` has zero members.

## Capacity and rollback

The LA peer is deliberately sized for `overseas-edge-50` (1 CPU/2 GiB): NetBird
requests 100m/128Mi, is limited to 800m/768Mi, and is capped to 150 Mbps by a
Pod-local TBF qdisc. Its identity is persisted at
`/var/lib/rancher/netbird/la`, and UDP 51820 is exposed as a host port for
direct WireGuard whenever the cloud firewall permits it. The LA Pod deliberately
uses `1.1.1.1` and `8.8.8.8` as its original DNS upstream: cluster DNS has been
observed returning poisoned answers for Google/YouTube, while domain resources
must resolve at the overseas routing peer. The restricted-node-6dc3f140f9 CN peer is now the canary
transparent Mihomo gateway. A separate `netbird-egress-probe` Linux client is
assigned to a probe-only Network whose sole router is that gateway; it prevents
LA-direct from making an acceptance test look successful. Its
`quality-benchmark` sidecar emits one JSON line every 15 minutes with gateway
loss/RTT, overseas DNS correctness, Google 204 and a 5 MB Cloudflare download
rate. This follows the latency/jitter/loss/bandwidth model used by community
tools such as `mihomo-speedtest-rs`, without running an unbuilt third-party
binary in production. NetBird route selection is primary/standby or nearest
latency, not bandwidth aggregation or ECMP.

Rollback by removing customer policies/setup keys, then scaling the three
Deployments to zero. Retain the PVC and Secrets. Remove the DNS publication
through its controller and remove the MikroTik UDP mapping only after clients
are disconnected.
