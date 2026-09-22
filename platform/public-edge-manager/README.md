# PublicEdge DNS authority operations

The Flux HelmRelease in `release.yaml` is the active GitOps declaration. Its
authority DaemonSet is restricted to exact Kubernetes Node names *and* the
`networking.re8ch.com/dns-authority-candidate=true` Node label. The label is
admission evidence, not a replacement for an external reachability probe.

`nameserver-election.py` scans live Node ExternalIPs without a Node Ready gate,
requires authoritative SOA answers on UDP and TCP 53 for every delegated child
zone, then selects one to three servers by region diversity and configured
bandwidth. It updates a projected runtime ConfigMap and verifies the new NS
answers before reconciling Cloudflare glue and delegation. A zero-candidate
result leaves the last valid delegation untouched and fails visibly. The
CronJob is initially suspended until the 0.5.5 authority release is healthy.
`dns-candidate-nodes.yaml` provides stable NS names for the two current nodes;
it is not an eligibility allowlist. restricted-node-6dc3f140f9 is not currently a public DNS
authority because its public UDP/TCP 53 probe fails. The external monitor
discovers parent NS and glue rather than expecting a fixed host count. It runs
from GitHub Actions every 15 minutes and checks every delegated zone.

`service.re8ch.com` and `functions.re8ch.com` are delegated child zones. Keep
first-level records through their client migration windows until every new
hostname passes an external route and certificate check.

A local Pod `/healthz` only proves process health. The election controller
probes each public IP directly and changes only NS delegation and glue; it
does not alter routers or service A records. The external monitor remains an
independent check on its results.
