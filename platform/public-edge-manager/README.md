# PublicEdge DNS authority operations

The Flux HelmRelease in `release.yaml` is the active GitOps declaration. Its
authority DaemonSet is restricted to exact Kubernetes Node names *and* the
`networking.re8ch.com/dns-authority-candidate=true` Node label. The label is
admission evidence, not a replacement for an external reachability probe.

`dns-candidate-nodes.yaml` admits `domain-ex` and `suxunji` after independent
UDP/TCP 53 checks against their actual public IPs on 2026-09-22. restricted-node-6dc3f140f9 is not a
public DNS authority. `authority-monitor.json` is the external monitoring
contract for delegated zones and their admitted public IPs. The scheduled
GitHub Actions workflow runs from outside the cluster every 15 minutes,
records the config digest and observed NS/A/SOA responses, and fails when a
delegation, UDP/TCP authority, or selected service answer is unavailable.

A local Pod `/healthz` only proves process health. If the external monitor
fails, do not add more delegated names; repair the affected public ingress or
withdraw its Node label and update the NS delegation through a reviewed GitOps
change. The monitor does not automatically edit DNS, labels, or routers.
