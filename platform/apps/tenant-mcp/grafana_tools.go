package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func (s *server) registerGrafanaTools(ms *mcp.Server) {
	definitions, err := legacyDefinitions()
	if err != nil {
		panic(err)
	}
	for _, name := range []string{"monitoring_catalog", "monitoring_query", "monitoring_analyze", "monitoring_dashboard"} {
		definition := definitions[name]
		if definition.Name == "" {
			panic("missing tenant schema: " + name)
		}
		mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations(name), Name: name, Description: definition.Description, InputSchema: definition.InputSchema},
			func(ctx context.Context, _ *mcp.CallToolRequest, args map[string]any) (*mcp.CallToolResult, any, error) {
				id, err := identityFrom(ctx)
				if err != nil {
					return nil, nil, err
				}
				if name == "monitoring_dashboard" {
					if err := requireOperator(id); err != nil {
						return nil, nil, err
					}
				}
				operation, err := monitoringOperation(name, args)
				if err != nil {
					return nil, nil, err
				}
				value, err := s.callGrafana(ctx, id.Tenant, operation, args)
				return nil, value, err
			})
	}
}

func monitoringOperation(name string, args map[string]any) (string, error) {
	action := asString(args, "action")
	switch name {
	case "monitoring_catalog":
		operations := map[string]string{
			"list_capabilities": "grafana_capability_list",
			"get_capability":    "grafana_capability_get",
			"list_datasources":  "grafana_datasource_list",
			"get_datasource":    "grafana_datasource_get",
		}
		if action == "get_capability" && asString(args, "capability_id") == "" {
			return "", errors.New("capability_id is required")
		}
		if action == "get_datasource" && asString(args, "datasource_uid") == "" {
			return "", errors.New("datasource_uid is required")
		}
		if operation := operations[action]; operation != "" {
			return operation, nil
		}
	case "monitoring_query":
		return "grafana_query", nil
	case "monitoring_analyze":
		operations := map[string]string{"pipeline": "grafana_analyze", "indicators": "grafana_indicators", "correlate": "grafana_correlate"}
		if action == "indicators" && len(slice(args["indicators"])) == 0 {
			return "", errors.New("indicators are required")
		}
		if operation := operations[action]; operation != "" {
			return operation, nil
		}
	case "monitoring_dashboard":
		operations := map[string]string{"render": "grafana_render", "save": "grafana_exploration_save", "delete": "grafana_exploration_delete"}
		if action == "render" && args["panel_id"] == nil {
			return "", errors.New("panel_id is required")
		}
		if action == "save" && (args["dashboard"] == nil || asString(args, "idempotency_key") == "") {
			return "", errors.New("dashboard and idempotency_key are required")
		}
		if action == "delete" && asString(args, "dashboard_uid") == "" {
			return "", errors.New("dashboard_uid is required")
		}
		if operation := operations[action]; operation != "" {
			return operation, nil
		}
	}
	return "", errors.New("unsupported monitoring action")
}

func withOperation(tenant, operation string, value map[string]any) map[string]any {
	value["operationId"], value["tenantRef"] = operation, tenant
	return value
}

func (s *server) callGrafana(ctx context.Context, tenant, name string, args map[string]any) (any, error) {
	operation := "op_" + newRef()
	if name == "grafana_capability_list" {
		manifest, err := grafanaCapabilities()
		if err != nil {
			return nil, err
		}
		return withOperation(tenant, operation, manifest), nil
	}
	if name == "grafana_capability_get" {
		manifest, err := grafanaCapabilities()
		if err != nil {
			return nil, err
		}
		for _, raw := range slice(manifest["items"]) {
			item := object(raw)
			if item["id"] == args["capability_id"] {
				return withOperation(tenant, operation, map[string]any{"capability": item, "capabilityManifestHash": manifest["capabilityManifestHash"]}), nil
			}
		}
		return nil, errors.New("Grafana capability is unknown")
	}
	// Numeric analysis only uses caller-supplied series or tenant result refs.
	// It must remain available when the tenant has no Grafana organization.
	localAnalysis := name == "grafana_analyze" || name == "grafana_indicators" ||
		(name == "grafana_correlate" && asString(args, "datasource_uid") == "")
	var client *grafanaClient
	var datasources []map[string]any
	var err error
	if !localAnalysis {
		client, err = tenantGrafana(ctx, tenant, false)
		if err != nil {
			return nil, err
		}
		datasources, err = grafanaDatasources(ctx, client)
		if err != nil {
			return nil, err
		}
	}
	switch name {
	case "grafana_datasource_list":
		items := []map[string]any{}
		for _, row := range datasources {
			items = append(items, datasourceCapability(row))
		}
		return withOperation(tenant, operation, map[string]any{"datasources": items}), nil
	case "grafana_datasource_get":
		for _, row := range datasources {
			if row["uid"] == args["datasource_uid"] {
				return withOperation(tenant, operation, map[string]any{"datasource": datasourceCapability(row)}), nil
			}
		}
		return nil, errors.New("datasource is not visible to the authenticated tenant")
	case "grafana_query":
		result, err := s.grafanaQuery(ctx, tenant, client, object(args["spec"]), datasources)
		if err != nil {
			return nil, err
		}
		return withOperation(tenant, operation, result), nil
	case "grafana_analyze", "grafana_indicators":
		series := map[string][]float64{}
		for key, raw := range object(args["series"]) {
			values, err := numericArray(raw)
			if err != nil {
				return nil, err
			}
			series[key] = values
		}
		if len(series) == 0 && asString(args, "result_ref") != "" {
			value, err := s.resultGet(tenant, asString(args, "result_ref"))
			if err != nil {
				return nil, err
			}
			series = framesToSeries(value)
		}
		if len(series) == 0 && args["values"] != nil {
			values, err := numericArray(args["values"])
			if err != nil {
				return nil, err
			}
			series["input"] = values
		}
		if len(series) == 0 {
			return nil, errors.New("numeric series or result_ref is required")
		}
		var result map[string]any
		if name == "grafana_analyze" {
			result, err = analyzeSeries(series, slice(args["pipeline"]))
		} else {
			if len(series) != 1 {
				return nil, errors.New("grafana_indicators requires exactly one numeric series")
			}
			for _, values := range series {
				var found map[string]any
				found, err = indicators(values, slice(args["indicators"]))
				result = map[string]any{"indicators": found}
			}
		}
		if err != nil {
			return nil, err
		}
		ref := s.resultPut(tenant, result)
		result["resultRef"], result["resultUri"] = ref, "re8ch://tenant/"+tenant+"/grafana/results/"+ref
		return withOperation(tenant, operation, result), nil
	case "grafana_correlate":
		uid := asString(args, "datasource_uid")
		if uid != "" {
			visible := false
			for _, row := range datasources {
				if row["uid"] == uid {
					visible = true
					break
				}
			}
			if !visible {
				return nil, errors.New("datasource is not visible to the authenticated tenant")
			}
			value, err := client.request(ctx, http.MethodGet, "/api/datasources/uid/"+url.PathEscape(uid)+"/correlations", nil, 30*time.Second, false)
			if err != nil {
				return nil, err
			}
			items := []map[string]any{}
			for _, raw := range slice(value) {
				row := object(raw)
				selected := map[string]any{}
				for _, key := range []string{"uid", "label", "description", "type", "targetUID", "config"} {
					selected[key] = row[key]
				}
				items = append(items, selected)
			}
			return withOperation(tenant, operation, map[string]any{"nativeCorrelations": items}), nil
		}
		left, err := numericArray(args["left"])
		if err != nil {
			return nil, err
		}
		right, err := numericArray(args["right"])
		if err != nil {
			return nil, err
		}
		if len(left) != len(right) || len(left) < 2 {
			return nil, errors.New("left and right must have the same length of at least two")
		}
		method := asString(args, "method")
		if method == "" {
			method = "pearson"
		}
		rows := lagCorrelations(left, right, method, intValue(args["max_lag"]))
		var best any
		strongest := -1.0
		for _, row := range rows {
			if coefficient, ok := row["coefficient"].(float64); ok && mathAbs(coefficient) > strongest {
				strongest = mathAbs(coefficient)
				best = row
			}
		}
		return withOperation(tenant, operation, map[string]any{"method": method, "correlations": rows, "best": best, "warning": "Correlation does not imply causation."}), nil
	}
	if name == "grafana_render" || name == "grafana_exploration_save" {
		binding, bindingErr := tenantGrafanaBinding(tenant)
		if bindingErr != nil {
			if client == nil || client.orgID <= 1 || !strings.HasPrefix(tenant, "personal-") {
				return nil, bindingErr
			}
			binding = grafanaBinding{OrgID: client.orgID, TenantLabel: tenant}
		}
		dashboard := object(args["dashboard"])
		if name == "grafana_render" && args["dashboard"] == nil {
			uid := asString(args, "dashboard_uid")
			if uid == "" {
				return nil, errors.New("dashboard_uid or dashboard is required")
			}
			current, readErr := client.request(ctx, http.MethodGet, "/api/dashboards/uid/"+url.PathEscape(uid), nil, 30*time.Second, false)
			if readErr != nil {
				return nil, readErr
			}
			dashboard = object(object(current)["dashboard"])
		}
		if err := validateGrafanaDashboardScope(dashboard, binding.TenantLabel, datasources); err != nil {
			return nil, err
		}
	}
	admin, err := tenantGrafana(ctx, tenant, true)
	if err != nil {
		return nil, err
	}
	switch name {
	case "grafana_render":
		return s.grafanaRender(ctx, tenant, operation, admin, args)
	case "grafana_exploration_save":
		return s.grafanaSave(ctx, tenant, operation, admin, args)
	case "grafana_exploration_delete":
		return s.grafanaDelete(ctx, tenant, operation, admin, args)
	}
	return nil, errors.New("tool is not published")
}

func mathAbs(value float64) float64 {
	if value < 0 {
		return -value
	}
	return value
}

func uniqueSortedTags(raw any, required ...string) []string {
	seen := map[string]bool{}
	for _, value := range slice(raw) {
		if item, ok := value.(string); ok {
			seen[item] = true
		}
	}
	for _, item := range required {
		seen[item] = true
	}
	result := []string{}
	for item := range seen {
		result = append(result, item)
	}
	sort.Strings(result)
	return result
}

func (s *server) grafanaRender(ctx context.Context, tenant, operation string, admin *grafanaClient, args map[string]any) (any, error) {
	uid := asString(args, "dashboard_uid")
	temporary := ""
	if args["dashboard"] != nil {
		temporary = "mcp-render-" + newRef()[:20]
		dashboard := map[string]any{}
		for key, value := range object(args["dashboard"]) {
			dashboard[key] = value
		}
		delete(dashboard, "id")
		dashboard["uid"], dashboard["tags"] = temporary, uniqueSortedTags(dashboard["tags"], "re8ch-ai-ephemeral", "tenant:"+tenant)
		if _, err := admin.request(ctx, http.MethodPost, "/api/dashboards/db", map[string]any{"dashboard": dashboard, "folderUid": "", "overwrite": false, "message": "Ephemeral tenant MCP render"}, 30*time.Second, false); err != nil {
			return nil, err
		}
		uid = temporary
	}
	if uid == "" {
		return nil, errors.New("dashboard_uid or dashboard is required")
	}
	query := url.Values{"panelId": {fmt.Sprint(intValue(args["panel_id"]))}, "from": {"now-1h"}, "to": {"now"}, "width": {"1000"}, "height": {"500"}, "tz": {"UTC"}}
	for key := range map[string]bool{"from": true, "to": true, "width": true, "height": true} {
		if value, ok := args[key]; ok {
			query.Set(key, fmt.Sprint(value))
		}
	}
	image, err := admin.request(ctx, http.MethodGet, "/render/d-solo/"+url.PathEscape(uid)+"?"+query.Encode(), nil, 60*time.Second, true)
	if temporary != "" {
		_, cleanupErr := admin.request(ctx, http.MethodDelete, "/api/dashboards/uid/"+temporary, nil, 30*time.Second, false)
		if err == nil && cleanupErr != nil {
			err = cleanupErr
		}
	}
	if err != nil {
		return nil, err
	}
	hash, err := grafanaHash(args)
	if err != nil {
		return nil, err
	}
	ref := s.resultPut(tenant, map[string]any{"blob": image.([]byte), "mimeType": "image/png", "queryHash": hash})
	return withOperation(tenant, operation, map[string]any{"imageRef": "re8ch://tenant/" + tenant + "/grafana/results/" + ref, "mimeType": "image/png", "expiresInSeconds": 900}), nil
}

func (s *server) grafanaSave(ctx context.Context, tenant, operation string, admin *grafanaClient, args map[string]any) (any, error) {
	dashboard := map[string]any{}
	for key, value := range object(args["dashboard"]) {
		dashboard[key] = value
	}
	delete(dashboard, "id")
	if asString(dashboard, "uid") == "" {
		digest := sha256.Sum256([]byte(tenant + ":" + asString(args, "idempotency_key")))
		dashboard["uid"] = "ai-" + hex.EncodeToString(digest[:])[:24]
	}
	dashboard["tags"] = uniqueSortedTags(dashboard["tags"], "re8ch-ai-exploration", "tenant:"+tenant)
	uid := asString(dashboard, "uid")
	current, err := admin.request(ctx, http.MethodGet, "/api/dashboards/uid/"+url.PathEscape(uid), nil, 30*time.Second, false)
	if err != nil {
		var failure grafanaHTTPError
		if !errors.As(err, &failure) || failure.Status != 404 {
			return nil, err
		}
	}
	if err == nil {
		tags := map[string]bool{}
		for _, value := range slice(object(object(current)["dashboard"])["tags"]) {
			if tag, ok := value.(string); ok {
				tags[tag] = true
			}
		}
		if !tags["re8ch-ai-exploration"] || !tags["tenant:"+tenant] {
			return nil, errors.New("a provisioned or non-exploration dashboard cannot be overwritten")
		}
	}
	folder := "ai-explorations-" + tenant
	if _, err = admin.request(ctx, http.MethodGet, "/api/folders/"+folder, nil, 30*time.Second, false); err != nil {
		var failure grafanaHTTPError
		if !errors.As(err, &failure) || failure.Status != 404 {
			return nil, err
		}
		if _, err = admin.request(ctx, http.MethodPost, "/api/folders", map[string]any{"uid": folder, "title": "AI Explorations"}, 30*time.Second, false); err != nil {
			return nil, err
		}
	}
	message := asString(args, "message")
	if message == "" {
		message = "Saved by tenant observability MCP"
	}
	saved, err := admin.request(ctx, http.MethodPost, "/api/dashboards/db", map[string]any{"dashboard": dashboard, "folderUid": folder, "overwrite": true, "message": message}, 30*time.Second, false)
	if err != nil {
		return nil, err
	}
	hash, err := grafanaHash(dashboard)
	if err != nil {
		return nil, err
	}
	return withOperation(tenant, operation, map[string]any{"dashboardUid": object(saved)["uid"], "url": object(saved)["url"], "idempotencyKey": args["idempotency_key"], "queryHash": hash}), nil
}

func (s *server) grafanaDelete(ctx context.Context, tenant, operation string, admin *grafanaClient, args map[string]any) (any, error) {
	uid := asString(args, "dashboard_uid")
	current, err := admin.request(ctx, http.MethodGet, "/api/dashboards/uid/"+url.PathEscape(uid), nil, 30*time.Second, false)
	if err != nil {
		return nil, err
	}
	tags := map[string]bool{}
	for _, value := range slice(object(object(current)["dashboard"])["tags"]) {
		if tag, ok := value.(string); ok {
			tags[tag] = true
		}
	}
	if !tags["re8ch-ai-exploration"] || !tags["tenant:"+tenant] {
		return nil, errors.New("only tenant-owned AI exploration dashboards may be deleted")
	}
	deleted, err := admin.request(ctx, http.MethodDelete, "/api/dashboards/uid/"+url.PathEscape(uid), nil, 30*time.Second, false)
	if err != nil {
		return nil, err
	}
	return withOperation(tenant, operation, map[string]any{"deleted": true, "title": object(deleted)["title"]}), nil
}
