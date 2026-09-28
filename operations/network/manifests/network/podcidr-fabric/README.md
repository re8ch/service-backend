# PodCIDR fabric address roles

`internalIp` is the retained ZeroTier/control-plane routing identity used by
the fabric reconciler. It is **not** necessarily the Kubernetes Node
`InternalIP`; replacing it with the AdvFab address changes BGP peers and
control-route selection.

`acceleratedIp` is the advertised AdvFab identity. For a node whose K3s
registration has explicitly migrated to that identity, `kubernetesNodeIp`
records the intended Kubernetes Node `InternalIP` without changing the
ZeroTier routing identity. The overseas-la agent registers as `10.250.0.104`;
its independent ZeroTier identity remains `10.181.22.104`.

`overseas-flk` and `overseas-la-2` also register through their accelerated
identities (`10.250.0.105` and `10.250.0.220`). Their ZeroTier `internalIp`
values remain BGP and fallback identities. On these transport peers, install
`operations/network/scripts/k3s-control-source-snat.py` and the matching
`private-transit/k3s-control-source-snat.service`, with this inventory copied
to `/etc/re8ch/podcidr-fabric-inventory.json`. The service selects the local
accelerated identity from loopback and uses it as source for outbound K3s
connections to R640 `10.250.0.128:6443`. Without this rule, Linux may use a
WireGuard transport address, which can leave R640's K3s remotedialer offline
even while the Node reports Ready.
