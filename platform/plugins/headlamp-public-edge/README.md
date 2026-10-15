# PublicEdge Headlamp plugin

Read-only topology console for PublicEdgeManager. It shows public candidates,
area/region, capacity, health, selected ExternalDNS target, Gateway listener and
backend Service for every public hostname. `Accepted` means the Gateway accepted
and resolved the route; `In-cluster path` additionally requires a selected public exit,
ready backend EndpointSlice and ready Pod. Empty parent conditions no longer
count as accepted. Candidate Ready is checked against a live Node and fresh
PublicEdge evidence. PowerDNS-authoritative zones and legacy published domains
are shown separately. The DNS view distinguishes the four public PowerDNS
authorities from the 0.7.x hidden-primary/secondary XoT and signed RouteSnapshot
topology. Missing workload API data is reported as unavailable instead of as
zero healthy. Authoritative rows derive their service-specific exit
from PublicEdge probes instead of obsolete ExternalDNS annotations. This does
not prove external DNS/TLS reachability; the
scheduled public authority monitor covers that separate boundary. The plugin
never patches DNS or routing.

The GlobalPing tab renders the controller-owned per-edge SNI and service-domain
quality snapshot, while the Candidates table shows the latest GlobalPing
readiness, probe count, latency and loss recorded on each PublicEdge status.

The Websites tab joins the reviewed public-site desired-state registry with
the external provider snapshot. It shows each logical site's global and China
deployments, origin allocation, CDN delivery, GEO/A2A declaration, quality
profile, and reconciliation state. These indicators are advisory and do not
write cloud configuration or enforce GitHub branch protection.

The plugin requires read access to `PublicEdge`, `Node`, `HTTPRoute`, `Ingress`, `Pod`,
`Deployment`, `DaemonSet`, `EndpointSlice` and the PublicEdge records `ConfigMap`.
