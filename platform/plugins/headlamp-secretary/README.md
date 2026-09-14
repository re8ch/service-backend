# RE8CH Headlamp Secretary

Read-only operational console for the tenant-scoped Secretary runtime. It shows
Kubernetes workload/configuration health and consumes the authenticated,
append-only Secretary event stream. Configuration readiness is derived only from
Deployment `secretKeyRef` metadata, so Secret objects and values are never requested
or rendered. The query
token is kept in browser `sessionStorage` and is not persisted by the plugin.
