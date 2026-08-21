# Cilium service-VIP fabric deployment record

## Outcome

The first RE8CH infrastructure application is an opt-in Cilium LB IPAM and FRR
service fabric. Kubernetes owns `192.0.2.215/24`; only allocated LoadBalancer
addresses are advertised as `/32`. Node identity (`192.0.2.253/24`) and Pod CIDR
(`192.0.2.224/16`) are explicitly outside the optimization domain.

The live canary is `192.0.2.137:4240`. From the ICP ingress router it resolves to
two equal-cost WireGuard next hops, A1 and B1. restricted-node-6dc3f140f9 advertises the same VIP to
both control-plane FRR routers and remains a public-edge and failure path. BGP
correctly prefers the shorter A1/B1 ingress paths instead of forcing an
asymmetric three-way ECMP.

## Problems encountered and responses

1. The Mac kubeconfig pointed at an obsolete ICP API context and returned API
   discovery `NotFound`. All writes stopped; `/readyz`, etcd, and the three
   control-plane nodes were checked through restricted-node-6dc3f140f9 before proceeding.
2. FRR had a systemd ordering cycle because it was bound to the single
   `wg-public-mesh` unit. FRR was redefined as a consumer of multiple independent
   fabrics so a failed mesh tunnel cannot take down private-transit BGP.
3. MikroTik owns ZeroTier address `192.0.2.183`; using it as restricted-node-6dc3f140f9's BGP source
   caused DNAT and return-path ambiguity. BGP was moved onto truthful addresses:
   A1/B1 use their InternalIP only for the ICP control session, while restricted-node-6dc3f140f9 uses
   its `192.0.2.205` transit identity toward A1/B1.
4. Local Cilium cannot peer with host FRR through the same local address. The
   handoff therefore uses cross-node peers and rewrites only service-VIP next
   hops into the `10.250` transit domain.
5. FRR rejected rewritten BGP next hops when they could only be recursively
   resolved through another BGP route. Stable transit identity `/32` routes were
   installed over the corresponding WireGuard interface; VIP routes remain
   dynamic.
6. The kernel selected the correct ECMP interfaces but WireGuard returned
   `Required key not available`. `192.0.2.215/24` was added to the appropriate
   peer AllowedIPs; TCP canary then succeeded.
7. The path scorer treated both 200 Mbps spokes as 1 Gbps. Capacity was corrected
   to 200 Mbps so utilization penalties and weighted splitting are meaningful.

## Operating architecture

```text
LoadBalancer Service (explicit two-label opt-in)
  -> Cilium LB IPAM allocates 10.251.x.x
  -> Cilium advertises exact /32
     A1 ----> ICP FRR ----> public mesh / ingress
     B1 ----> ICP FRR ----> public mesh / ingress
     restricted-node-6dc3f140f9 --> A1 FRR + B1 FRR (public edge and backup)
  -> FRR resolves data next hops through 10.250 identities
  -> WireGuard/private-transit carries packets
  -> Cilium performs Kubernetes service load balancing
```

The existing policy agent adds a second decision layer. It probes path RTT and
loss, measures interface utilization, applies a hold-down, and writes weighted
multipath routes. Its JSON journal is already a time-series event stream, but a
durable Prometheus/TSDB exporter is still required before ML training data can
be considered complete.

## Helm lifecycle

Render and validate first:

```sh
helm lint manifests/k3s/cilium/service-vip-fabric
helm template re8ch-network-fabric manifests/k3s/cilium/service-vip-fabric
```

Install only after the three control-plane readiness and FRR policy gates pass:

```sh
helm upgrade --install re8ch-network-fabric \
  manifests/k3s/cilium/service-vip-fabric --namespace kube-system
```

Production Services remain excluded until both opt-in labels are present.
Rollback removes those labels first, verifies `/32` withdrawal, then disables
the pool. It never changes Node InternalIP, Pod CIDR, etcd, or public DNS.

## Storage dependency boundary

CephFS may consume this fabric, but storage failure must not withdraw or mutate
the network control plane. Before repairing Ceph, inventory every PV/PVC,
hostPath/local-path directory, mount, backing block device, OSD UUID, and actual
workload node. Existing data is classified before any new local volume or OSD is
created. CephFS becomes an additional shared StorageClass with `Retain`; it does
not silently replace existing local-path volumes or become default during the
repair.
