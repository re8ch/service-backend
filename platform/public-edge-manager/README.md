# PublicEdge DNS authority operations

The Flux HelmRelease in `release.yaml` is the active GitOps declaration. It
contains service catalog and model parameters only. The controller derives
eligible edge and authority nodes from all live Kubernetes Nodes. It uses
uplink and region facts projected from `inventory/nodes.yaml` when available,
then Node capacity annotations and topology labels for unlisted nodes. A
global Node ExternalIP, fresh
NetworkPathAssessment evidence, the labelled canonical Gateway, and direct
public listener probes. `inventory/nodes.yaml` is shared with AdvancedFabric;
the release does not keep its own capacity or region list. Unknown uplink
capacity is ineligible; listed nodes are not automatically Ready. No fixed edge address
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

`test.re8ch.com` is the delegated authority for ephemeral tenant previews.
The zone declaration contains no wildcard or application A record. Publish a
preview hostname only after the tenant route broker proves namespace and
Service ownership, dedicated Gateway listener attachment, TLS, and internal
health. PublicEdge's existing authority readiness gates control parent NS
publication; verify the parent delegation and UDP/TCP 53 answers from outside
the cluster before accepting preview traffic.

A local Pod `/healthz` only proves process health. The discovery controller
probes each public IP directly and changes only derived eligibility, NS
delegation and glue; it does not alter routers. The external monitor remains
an independent check on its results.

## XFR-over-TLS migration gate

GitHub `main` remains the only desired-state authority. The OCI artifacts are
commit-derived delivery objects. `overseas-flk` is the hidden primary transport
role, not another configuration source. Zone data uses mTLS on TCP 853;
regional state uses an mTLS RouteSnapshot endpoint plus Ed25519 signatures.
Secondaries retain the last verified snapshot on transport, expiry, signature,
or rollback failure.

All seven zones enter shadow synchronization together, but delegation changes
remain zone-by-zone. Run
`operations/network/scripts/validate-public-edge-cutover.sh` against every
candidate authority before changing apex or parent NS. NXDOMAIN, serial/NS
mismatch, or an incorrect CN/US/EU answer stops rollout. Retain old NS records
for two maximum-TTL intervals and until recursive probes no longer see them.

RouterOS is absent from the DNS authority set. `223.159.210.222` is only the
default CN PublicEdge transport; an unhealthy backend becomes a controlled
HTTP 503 at PublicEdge, never DNS NXDOMAIN for a declared service.
