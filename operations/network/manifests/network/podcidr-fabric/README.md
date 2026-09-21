# PodCIDR fabric address roles

`internalIp` is the retained ZeroTier/control-plane routing identity used by
the fabric reconciler. It is **not** necessarily the Kubernetes Node
`InternalIP`; replacing it with the AdvFab address changes BGP peers and
control-route selection.

`acceleratedIp` is the advertised AdvFab identity. For a node whose K3s
registration has explicitly migrated to that identity, `kubernetesNodeIp`
records the intended Kubernetes Node `InternalIP` without changing the
ZeroTier routing identity. The overseas-la agent registers as `192.0.2.147`;
its independent ZeroTier identity remains `192.0.2.110`.
