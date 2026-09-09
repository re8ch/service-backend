export type ToolGroup = {
  category: string;
  resourceClass: string;
  disposition: 'retained' | 'local' | 'external';
  tools: string[];
};

export const TENANT_TOOL_GROUPS: ToolGroup[] = [
  {
    category: 'Workloads & volumes', resourceClass: 'compute.workload.v1 / storage.volume.v1', disposition: 'local',
    tools: ['tenant-capabilities-list', 'tenant-claim-plan', 'tenant-claim-create', 'tenant-claim-get', 'tenant-claim-list', 'tenant-claim-update', 'tenant-claim-renew', 'tenant-claim-cancel', 'tenant-usage-summary-get'],
  },
  {
    category: 'Service lifecycle', resourceClass: 'all service classes', disposition: 'local',
    tools: ['service-catalog-list', 'service-instance-plan', 'service-instance-create', 'service-instance-get', 'service-instance-list', 'service-instance-update', 'service-instance-suspend', 'service-instance-resume', 'service-instance-delete', 'service-binding-create', 'service-binding-list', 'service-binding-revoke', 'usage-summary-get', 'operation-get'],
  },
  {
    category: 'Harbor registry', resourceClass: 'registry.harbor.v1', disposition: 'retained',
    tools: ['harbor-project-get', 'harbor-repository-list', 'harbor-artifact-list', 'harbor-artifact-get', 'harbor-artifact-scan-start', 'harbor-vulnerability-report-get', 'harbor-sbom-get', 'harbor-accessory-list', 'harbor-tag-create', 'harbor-tag-delete', 'harbor-audit-log-list', 'harbor-build-start', 'harbor-build-get', 'harbor-build-list', 'harbor-build-cancel'],
  },
  {
    category: 'Grafana observability', resourceClass: 'observability.grafana.v1', disposition: 'local',
    tools: ['grafana-capabilities', 'grafana-capability-get', 'grafana-datasource-list', 'grafana-datasource-get', 'grafana-query', 'grafana-render', 'grafana-analyze', 'grafana-correlate', 'grafana-indicators', 'grafana-exploration-save', 'grafana-exploration-delete'],
  },
  {
    category: 'BYOC', resourceClass: 'external cluster identity', disposition: 'external',
    tools: ['byoc-cluster-register', 'byoc-registration-list', 'byoc-connection-list', 'byoc-connection-revoke'],
  },
  {
    category: 'Artc Kubernetes', resourceClass: 'network.among-clusters.v1', disposition: 'external',
    tools: ['artc-kubernetes-read', 'artc-kubernetes-apply', 'artc-kubernetes-admin'],
  },
];

export const TENANT_TOOL_COUNT = TENANT_TOOL_GROUPS.reduce((sum, group) => sum + group.tools.length, 0);
