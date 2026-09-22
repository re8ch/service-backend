# PublicEdge candidate ownership

PublicEdge is a rule-based path optimizer. It consumes `PublicEdge` declarations,
probe results, and optional fabric evidence; it does not own node inventory,
network reachability, or provider-specific DNS implementation.

The cluster-side adapter is the sole owner of candidate declarations. Its input
is the explicitly approved candidate configuration, not every Kubernetes Node.
For each configured candidate it must verify that the referenced Node exists,
the endpoint and forwarding configuration are present, and any required public
reachability qualification has passed. It must withdraw or disable a declaration
when that qualification is lost and remove orphan declarations when a candidate
is removed from cluster configuration. Kubernetes `NodeReady` is not a public
edge qualification or an automatic route veto. A fresh, reachable, externally
measured path can remain eligible while the kubelet is NotReady.

The open-source manager must accept an empty chart `publicEdges` list so this
adapter can own the resources without Helm ownership conflicts. The manager
then ranks only admitted candidates by its service-class, locality, capacity,
priority, probe, and evidence rules. DNS publication is an output adapter; DNS
record storage, router/NAT, eBGP, and node inventory remain external contracts.

Current transition: the Helm release still owns the Guangzhou and Loudi
declarations. The other live declarations are legacy unmanaged resources. Do
not set `publicEdges: []` until a cluster-side adapter owns all intended
candidates and the public-edge-manager chart with external-candidate support
has passed CI and been released. The abandoned `overseas-edge-50` was removed
from the live candidate set on 2026-09-22; its inventory entry is retained as
`not-observed` pending a separate infrastructure-retirement audit.
