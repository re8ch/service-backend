# Mihomos Cluster application architecture

## Purpose

Mihomos Cluster gives every Kubernetes node the same outbound policy surface
without making a single proxy node authoritative. Each node is always a
Consumer. A labelled, Ready node may additionally be a CN or Global Provider.

## Routing contract

| Consumer location | Chinese destination | International destination |
| --- | --- | --- |
| CN | direct | nearest available Global Provider |
| Global | nearest available CN Provider | direct |

Private RFC1918, carrier-grade NAT and cluster management ranges always remain
direct. `ipinfo.io` is also direct and is reserved for observing the physical
upstream identity. Cilium, FRR and BGP remain responsible for Pod, node and
Service reachability; Mihomo does not advertise routes or replace Gateway API.

## Kubernetes authority

The `mihomos-cluster` Helm release in `egress-fabric` is the only authority for
the node consumers, exit providers, headless Services, pinned CN CIDR rules,
telemetry agent and status-reader RBAC. Older temporary Mihomo DaemonSets and
Services must not coexist with the release because all use host-network ports.

Provider discovery uses Ready endpoints from these Services:

- `mihomos-cn-exit:1080`
- `mihomos-global-exit:1080`

The labels are:

- `networking.re8ch.com/geo=cn|global`
- `networking.re8ch.com/mihomo-exit-cn=enabled`
- `networking.re8ch.com/mihomo-exit=enabled`

## Telemetry and Headlamp

Each status agent performs a direct public-IP lookup and proxied HTTP checks.
Expected non-2xx results such as GHCR and OpenAI `401` or Google `204` count as
healthy. Consumer health is based on real proxied requests; controller health is
retained separately as diagnostic data.

The Headlamp plugin has two independent views:

- Provider: only exit nodes, with CN exits showing CN probes and Global exits
  showing international probes.
- Consumer: every observed node, with both probe families and no provider role
  column.

The plugin is read-only. It cannot select Candidates or mutate the data plane.

## restricted-node-6dc3f140f9 boundary

restricted-node-6dc3f140f9 is physically in Hunan. RouterOS must not globally policy-route
`192.0.2.169` through an overseas WireGuard table. restricted-node-6dc3f140f9's K3s services use
the local Mihomo Consumer for destination-aware egress. LAN, ZeroTier, DNAT
symmetric return and A8 IMS routes remain independent exceptions.
