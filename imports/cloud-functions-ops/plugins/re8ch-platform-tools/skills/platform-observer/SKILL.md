---
name: platform-observer
description: Inspect RE8CH platform and tenant health through the governed read-only ToolHive MCP surface. Use for Kubernetes inventory, tenant metrics, Harbor/Registry/Grafana/Headlamp/CloudBeaver health, and Pigsty route reachability.
---

# RE8CH Platform Observer

Use the `re8ch-platform-observe` MCP server for evidence-backed infrastructure
inspection. Start with `get_platform_service_health`, then use
`get_tenant_metrics_summary` and `list_k8s_resources` only for the affected
domain.

Treat `unreachable` as a finding, not permission to change an endpoint or
restart a workload. This plugin is read-only. Do not request credentials,
kubeconfigs, generic SQL, or generic Kubernetes proxy access. Refer write or
publication changes to an explicitly approved internal operator/admin workflow.

When reporting an incident, include the service domain, endpoint, observed
status, relevant tenant metric, and the matching Grafana or Headlamp link.
