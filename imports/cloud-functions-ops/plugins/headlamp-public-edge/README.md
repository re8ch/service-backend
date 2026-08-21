# PublicEdge Headlamp plugin

Read-only topology console for PublicEdgeManager. It shows public candidates,
area/region, capacity, health, selected ExternalDNS target, Gateway listener and
backend Service for every public hostname. It never patches DNS or routing.

The plugin requires read access to `PublicEdge`, `HTTPRoute`, `Ingress`, `Pod`
and `EndpointSlice` resources.
