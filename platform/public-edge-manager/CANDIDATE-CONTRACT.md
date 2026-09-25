# PublicEdge candidate ownership

PublicEdge is a rule-based path optimizer and discovery controller. Kubernetes
and the existing network inventory remain the fact authorities; this component
does not introduce a second node configuration store.

The controller is the sole owner of PublicEdge resources. Every reconciliation
derives candidates from all Nodes and rejects addresses that are not global,
stale or unreachable. A fresh, reachable NetworkPathAssessment plus a
programmed canonical Gateway is required. Public 80/443 probes qualify ingress;
UDP and TCP 53 probes separately qualify DNS authority. Losing evidence removes
the derived resource and labels idempotently.

Kubernetes `NodeReady` is not by itself a public-edge qualification. The fabric
assessment is the network authority and may explicitly permit a measured path
while kubelet state is unavailable. Router/NAT, eBGP and provider topology stay
in infrastructure inventory and are consumed as facts rather than copied into
the Helm release.
