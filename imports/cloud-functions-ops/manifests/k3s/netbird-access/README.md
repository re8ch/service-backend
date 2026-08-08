# RE8CH NetBird access service

This directory deploys a single-account NetBird Community control plane at
`access.re8ch.com:8443` plus two container-isolated routing peers. The peers run in
Pod network namespaces on `restricted-node-6dc3f140f9` and `overseas-edge-50`; they do not use
`hostNetwork` and must not change host routes, Cilium, FRR, or Kubernetes node
identity addresses.

## Hard gates

Do not apply this stack while K3s `/readyz` reports an etcd failure or any node
is not Ready. Run `preflight.sh` through restricted-node-6dc3f140f9 first. The stack also requires the
default `local-path` StorageClass and the existing wildcard TLS Secret in the
`llm-gateway` namespace. The single-replica server and its local volume are
pinned to restricted-node-6dc3f140f9; back up the PVC before moving the control plane.

The current public origin is `192.0.2.79`. The DNS controller owns the A
record. MikroTik must forward public UDP 3478 to restricted-node-6dc3f140f9 UDP NodePort 30478; this
is an edge-router change, not a Kubernetes node route change. Do not proxy this
hostname through Cloudflare because NetBird needs gRPC, WebSocket, relay, and
STUN behavior end to end.

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

`netbird-geodb-sync` runs daily at 03:17 and reads maintained Clash/Mihomo
GeoSite category YAML from `MetaCubeX/meta-rules-dat`. It reduces category
members to registrable-domain wildcards plus a small root-domain allowlist,
then upserts only `geodb:*` resources into `GeoDB Overseas via LA`. The job
prints a SHA-256 for every fetched category. Pruning is disabled by default, so
a failed or truncated upstream update cannot remove the last working rules.
The API token belongs to the `geodb-sync` Network Admin service user and exists
only in the `netbird-geodb-sync` Secret.

## Capacity and rollback

The LA peer is deliberately sized for `overseas-edge-50` (1 CPU/2 GiB): NetBird
requests 100m/128Mi, is limited to 800m/768Mi, and is capped to 150 Mbps by a
Pod-local TBF qdisc. Its identity is persisted at
`/var/lib/rancher/netbird/la`, and UDP 51820 is exposed as a host port for
direct WireGuard whenever the cloud firewall permits it. The LA Pod deliberately
uses `1.1.1.1` and `8.8.8.8` as its original DNS upstream: cluster DNS has been
observed returning poisoned answers for Google/YouTube, while domain resources
must resolve at the overseas routing peer. The CN peer is scaled to zero.
NetBird HA selects primary/standby or nearest latency; it is not ECMP.

Rollback by removing customer policies/setup keys, then scaling the three
Deployments to zero. Retain the PVC and Secrets. Remove the DNS publication
through its controller and remove the MikroTik UDP mapping only after clients
are disconnected.
