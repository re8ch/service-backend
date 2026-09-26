# PublicEdge DNS authority operations

The Flux HelmRelease in `release.yaml` is the active GitOps declaration. It
contains service catalog and model parameters only. The controller derives
eligible edge and authority nodes from membership and uplink data projected
from `inventory/nodes.yaml`, Node ExternalIP, fresh
NetworkPathAssessment evidence, the labelled canonical Gateway, and direct
public listener probes. `inventory/nodes.yaml` is shared with AdvancedFabric;
the release does not keep its own capacity or region list. Unlisted nodes are
ineligible, but a listed node is not automatically Ready. No fixed edge address
is accepted.

Two controller replicas use a Kubernetes Lease for discovery and Cloudflare
publication. The leader creates controller-owned PublicEdge objects, applies
derived scheduling labels, and maintains a projected runtime nameserver set.
Authority names are stable hashes of Node UID. UDP and TCP 53 must both answer
for every delegated child zone before a server is published. One authority is
an alerted degraded state; zero keeps the last known good delegation.

`service.re8ch.com` and `functions.re8ch.com` are delegated child zones. Keep
first-level records through their client migration windows until every new
hostname passes an external route and certificate check.

A local Pod `/healthz` only proves process health. The discovery controller
probes each public IP directly and changes only derived eligibility, NS
delegation and glue; it does not alter routers. The external monitor remains
an independent check on its results.
