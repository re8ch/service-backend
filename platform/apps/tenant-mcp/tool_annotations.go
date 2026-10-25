package main

import "github.com/modelcontextprotocol/go-sdk/mcp"

// Classify the entire supported behavior, including apply=true and all actions
// of multiplexed tools. These are client hints, never authorization controls.
// Keep names explicit so a newly published tool requires a metadata review.
func toolAnnotations(name string) *mcp.ToolAnnotations {
	readOnly, destructive, openWorld := false, false, false
	switch name {
	case "tenant_access_status", "tenant_organization_context_list", "tenant_self_service_capabilities", "tenant_capabilities_list", "tenant_claim_plan", "tenant_claim_get", "tenant_claim_list", "tenant_usage_summary_get",
		"tenant_namespace_list", "byoc_registration_list", "byoc_connection_list",
		"service_catalog_list", "service_instance_plan", "service_instance_get", "service_instance_list", "service_binding_list",
		"usage_summary_get", "usage_events_list", "operation_get", "dynamic_skill_search", "dynamic_skill_load", "platform_tool_catalog",
		"harbor_project_get", "harbor_repository_list", "harbor_artifact_list", "harbor_artifact_get", "harbor_vulnerability_report_get",
		"harbor_sbom_get", "harbor_accessory_list", "harbor_audit_log_list", "harbor_build_get", "harbor_build_list",
		"runtime_image_catalog",
		"grafana_capability_list", "grafana_capability_get", "grafana_datasource_list", "grafana_datasource_get",
		"grafana_query", "grafana_analyze", "grafana_indicators", "grafana_correlate",
		"monitoring_catalog", "monitoring_query", "monitoring_analyze", "tenant_kubernetes_get", "artc_kubernetes_read":
		readOnly = true
	case "tenant_secret_metadata_list", "tenant_exposure_list", "tenant_exposure_get":
		readOnly = true
	case "byoc_cluster_register", "tenant_namespace_create", "service_binding_create", "tenant_human_access_issue", "harbor_artifact_scan_start", "harbor_tag_create":
		// Additive operations against the configured tenant platform.
	case "tenant_secret_upsert":
		// Replaces existing write-only Secret data and cannot restore prior values.
		destructive = true
	case "tenant_exposure_create":
		// Reconciles an externally reachable endpoint through the platform PublicEdge.
		openWorld = true
	case "dynamic_skill_submit":
		// Stores caller-supplied workflow text for an independently reviewed platform process.
		openWorld = true
	case "tenant_claim_create", "service_instance_create":
		// Workloads/builds may fetch tenant-selected images or source repositories.
		openWorld = true
	case "tenant_claim_update", "service_instance_update", "tenant_kubernetes_apply", "artc_kubernetes_apply", "artc_kubernetes_admin", "platform_tool_call", "harbor_build_start", "runtime_image_build":
		// Can replace existing state and execute tenant-selected workloads/commands.
		destructive, openWorld = true, true
	case "tenant_claim_renew", "tenant_claim_cancel", "tenant_namespace_delete", "byoc_connection_revoke",
		"service_instance_suspend", "service_instance_resume", "service_instance_delete", "service_binding_revoke",
		"harbor_tag_delete", "harbor_build_cancel", "runtime_image_manage", "grafana_exploration_save", "grafana_exploration_delete",
		"grafana_render", "monitoring_dashboard", "tenant_kubernetes_delete", "tenant_secret_delete", "tenant_exposure_delete", "tenant_organization_context_select", "platform_organization", "platform_user":
		// Includes updates/revocations and render's temporary dashboard cleanup.
		destructive = true
	default:
		panic("missing reviewed MCP tool annotations: " + name)
	}
	return &mcp.ToolAnnotations{ReadOnlyHint: readOnly, DestructiveHint: &destructive, OpenWorldHint: &openWorld}
}
