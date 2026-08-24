# Mihomos Cluster

Mihomos Cluster is a Kubernetes egress fabric in which every labelled node
consumes a node-local Mihomo proxy and selected nodes also provide healthy CN or
Global exits. It is designed to complement Cilium routing: Cilium carries
cluster traffic, while Mihomo applies destination geography policy to traffic
leaving the cluster.

## Install

```bash
helm install mihomos-cluster \
  oci://ghcr.io/re8ch/charts/mihomos-cluster \
  --version 0.1.1 \
  --namespace egress-fabric --create-namespace
```

Label consumers and providers before installation:

```bash
kubectl label node NODE networking.re8ch.com/geo=cn
kubectl label node CN_EXIT networking.re8ch.com/mihomo-exit-cn=enabled
kubectl label node GLOBAL_EXIT networking.re8ch.com/mihomo-exit=enabled
```

CN consumers send international destinations to an available Global provider;
Global consumers send Chinese destinations to an available CN provider. Private
and cluster CIDRs remain direct. The public identity lookup URL also bypasses
Mihomo so telemetry observes the node's real upstream route.

## Components

- `mihomos-node-cn` and `mihomos-node-global`: node-local consumers on port 17891.
- `mihomos-cn-exit` and `mihomos-global-exit`: Ready-only provider candidates.
- `mihomos-status-agent`: per-node public identity and reachability telemetry.
- Headless Services: Candidate Selection inputs; NotReady providers are removed.

The optional Headlamp plugin is published separately and reads only labelled
status ConfigMaps. See the repository Runbook before changing node labels,
RouterOS policies or provider reachability.
