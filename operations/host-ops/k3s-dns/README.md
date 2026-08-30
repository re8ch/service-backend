# Advanced Fabric cluster DNS host configuration

Install `20-advanced-fabric-dns.yaml` under
`/etc/rancher/k3s/config.yaml.d/` on every server and agent. Install
`30-disable-coredns.yaml` only on every server/control-plane node.

The Advanced Fabric service `192.0.2.149` is the kubelet DNS address. The
declarative `kube-dns` compatibility service preserves `192.0.2.240` for Pods
created before the node rollout. Restart K3s one control-plane node at a time,
waiting for node, etcd, API, Cilium and DNS readiness between nodes.
