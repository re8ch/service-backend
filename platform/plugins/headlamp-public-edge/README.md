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
If the aggregate ConfigMap is unavailable to the Headlamp session, the panel
falls back to the controller-owned `status.externalQuality` on each PublicEdge.

The Websites tab joins the reviewed public-site desired-state registry with
the external provider snapshot. It shows each logical site's global and China
deployments, origin allocation, CDN delivery, GEO/A2A declaration, quality
profile, and reconciliation state. These indicators are advisory and do not
write cloud configuration or enforce GitHub branch protection.

The plugin requires read access to `PublicEdge`, `Node`, `HTTPRoute`, `Ingress`, `Pod`,
`Deployment`, `DaemonSet`, `EndpointSlice` and the PublicEdge records `ConfigMap`.

## Local and federated observations

Candidates discovered through AmongCluster show their declared publisher and Service
identity, endpoint ID, observed AF source, expiry and rejection reasons. A remote
node is never looked up in this console's local Node inventory. Typed candidates
require current generation-bound federation, scoped AF and backend observations;
unbound legacy candidates or missing observations remain Unknown. This read-only
view does not replace the authority's eligibility policy or issue new probes.

Service routes distinguish local Pod failures from ready EndpointSlice addresses
whose remote Pod is not observable here; the latter remain Unknown. A GlobalPing
0/0 or incomplete observation is not displayed as a measured failure or success.

Run `node --test tests/evidenceModel.test.mjs`, `npm run tsc` and `npm run build`.
Model tests use the actual typed publication shape, including UID replacement,
expiry, rejected AF evidence and local/remote inventory boundaries.
