package main

import (
	"errors"
	"fmt"
	"strings"
)

// Dashboard JSON is executed by Grafana during rendering, not by grafana_query.
// Accept only explicit tenant-scoped metrics targets until other datasource
// query languages have equivalent structural tenant-boundary validation.
func validateGrafanaDashboardScope(dashboard map[string]any, tenant string, datasources []map[string]any) error {
	if len(dashboard) == 0 {
		return errors.New("dashboard is required")
	}
	for key := range dashboard {
		switch key {
		case "id", "uid", "title", "description", "tags", "panels", "schemaVersion", "version", "timezone", "refresh", "time", "timepicker", "editable", "graphTooltip", "fiscalYearStartMonth", "weekStart", "style", "templating", "annotations", "links":
		default:
			return fmt.Errorf("dashboard field %s is not supported for tenant rendering", key)
		}
	}
	for _, key := range []string{"templating", "annotations"} {
		if value := dashboard[key]; value != nil && len(slice(object(value)["list"])) != 0 {
			return fmt.Errorf("dashboard %s queries are not supported for tenant rendering", key)
		}
	}
	if len(slice(dashboard["links"])) != 0 {
		return errors.New("dashboard links are not supported for tenant rendering")
	}
	visible := map[string]bool{}
	for _, source := range datasources {
		if kinds := datasourceCapability(source)["queryKinds"].([]string); len(kinds) == 1 && kinds[0] == "promql" {
			visible[asString(source, "uid")] = true
		}
	}
	panels := slice(dashboard["panels"])
	if len(panels) == 0 {
		return errors.New("dashboard requires at least one panel")
	}
	for _, raw := range panels {
		if err := validateGrafanaPanelScope(object(raw), tenant, visible); err != nil {
			return err
		}
	}
	return nil
}

func validateGrafanaPanelScope(panel map[string]any, tenant string, visible map[string]bool) error {
	if len(panel) == 0 || panel["libraryPanel"] != nil || panel["repeat"] != nil || panel["links"] != nil {
		return errors.New("library, linked, and repeated panels are not supported for tenant rendering")
	}
	for key := range panel {
		switch key {
		case "id", "type", "title", "description", "gridPos", "targets", "datasource", "fieldConfig", "options", "transparent", "collapsed", "panels", "transformations", "pluginVersion", "interval", "maxDataPoints", "timeFrom", "timeShift", "hideTimeOverride":
		default:
			return fmt.Errorf("dashboard panel field %s is not supported", key)
		}
	}
	panelType := asString(panel, "type")
	switch panelType {
	case "timeseries", "stat", "gauge", "table", "bargauge", "text", "row":
	default:
		return errors.New("dashboard panel type is not supported for tenant rendering")
	}
	if panelType != "row" && len(slice(panel["panels"])) != 0 {
		return errors.New("nested panels require a row panel")
	}
	if panelType != "text" && panelType != "row" && len(slice(panel["targets"])) == 0 {
		return errors.New("dashboard data panel requires at least one target")
	}
	if (panelType == "text" || panelType == "row") && len(slice(panel["targets"])) != 0 {
		return errors.New("text and row panels cannot include query targets")
	}
	if nested := slice(panel["panels"]); len(nested) != 0 {
		for _, raw := range nested {
			if err := validateGrafanaPanelScope(object(raw), tenant, visible); err != nil {
				return err
			}
		}
	}
	for _, raw := range slice(panel["targets"]) {
		target := object(raw)
		for key := range target {
			switch key {
			case "datasource", "expr", "refId", "legendFormat", "interval", "format", "hide", "instant", "range", "exemplar", "maxDataPoints", "intervalFactor":
			default:
				return fmt.Errorf("dashboard target field %s is not supported", key)
			}
		}
		uid := asString(object(target["datasource"]), "uid")
		if uid == "" {
			uid = asString(object(panel["datasource"]), "uid")
		}
		if !visible[uid] {
			return errors.New("dashboard datasource is not a tenant-visible metrics datasource")
		}
		if strings.TrimSpace(asString(target, "expr")) == "" {
			return errors.New("dashboard metrics target requires expr")
		}
		if err := validateMetricsTenantScope(asString(target, "expr"), tenant); err != nil {
			return err
		}
	}
	return nil
}
