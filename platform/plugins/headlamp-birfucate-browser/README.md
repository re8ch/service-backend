# Birfucate Browser for Headlamp

Read-only browsing of tenant resource consumption and versioned showback cost.
The page includes a Tenant → Resource type → Purpose flow and queries the
allowlisted browser API owned by the Birfucate Chart through the Kubernetes
Service Proxy and renders:

- Occupancy: current occupied capacity and accumulated resource-time.
- Bifurcation: behavior occurrences, intensity, fanout and accumulated score.
- Resource branches: meter/domain branches observed for each tenant resource.
- Tenant contract: lifecycle, visibility, hosting mode, BYOC policy, runtime and
  business namespaces, OIDC group bindings and granted capabilities.
- Circular resource corridors: owned namespace workloads and granted facilities
  classified as retained/returned, tenant-local consumption, or external-tenant
  bifurcation, including observed AmongClusters/BYOC destinations.
- Complete Re8ch Tenant MCP surface coverage, grouped by resource class; the
  checked-in catalog is tested against the PluginDefinition allowlist.

The plugin performs only HTTP GET requests. It contains no create, update,
delete, binding, credential, pricing or billing controls. The tenant selector
is a presentation filter, not an authorization boundary; access to the
Birfucate service proxy must be restricted by Headlamp/Kubernetes RBAC. The
browser never receives the VictoriaMetrics service address or arbitrary PromQL
access.

```sh
npm ci
npm run tsc
npm run build
npm run package
```
