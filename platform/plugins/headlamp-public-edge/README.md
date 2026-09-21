# PublicEdge Headlamp plugin

Read-only topology console for PublicEdgeManager. It shows public candidates,
area/region, capacity, health, selected ExternalDNS target, Gateway listener and
backend Service for every public hostname. `Accepted` means the Gateway accepted
and resolved the route; `Serving` additionally requires a selected public exit,
ready backend EndpointSlice and ready Pod. Empty parent conditions no longer
count as accepted. Candidate Ready is checked against a live Node and fresh
PublicEdge evidence. The plugin never patches DNS or routing.

The plugin requires read access to `PublicEdge`, `Node`, `HTTPRoute`, `Ingress`, `Pod`
and `EndpointSlice` resources.
