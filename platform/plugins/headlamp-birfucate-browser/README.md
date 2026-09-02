# Birfucate Browser for Headlamp

Read-only browsing of tenant resource consumption. The page queries VictoriaMetrics through the Kubernetes Service Proxy and renders:

- Occupancy: current occupied capacity and accumulated resource-time.
- Bifurcation: behavior occurrences, intensity, fanout and accumulated score.
- Resource branches: meter/domain branches observed for each tenant resource.

The plugin performs only HTTP GET requests. It contains no create, update, delete, binding, credential, pricing or billing controls. The tenant selector is a presentation filter, not an authorization boundary; access to the metrics service proxy must be restricted by Headlamp/Kubernetes RBAC.

```sh
npm ci
npm run tsc
npm run build
npm run package
```
