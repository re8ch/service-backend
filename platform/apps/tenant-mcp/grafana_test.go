package main

import (
	"context"
	"testing"
)

func TestGrafanaLocalAnalysisWithoutTenantBinding(t *testing.T) {
	s := testServer(t)
	t.Setenv("GRAFANA_TENANT_BINDINGS_JSON", "")
	for _, test := range []struct {
		name string
		args map[string]any
	}{
		{"grafana_analyze", map[string]any{"series": map[string]any{"sample": []any{1.0, 2.0, 3.0}}}},
		{"grafana_indicators", map[string]any{"values": []any{1.0, 2.0, 3.0}, "indicators": []any{map[string]any{"type": "movingAverage", "window": float64(2)}}}},
		{"grafana_correlate", map[string]any{"left": []any{1.0, 2.0, 3.0}, "right": []any{2.0, 4.0, 6.0}}},
	} {
		if _, err := s.callGrafana(context.Background(), "openai-review-demo", test.name, test.args); err != nil {
			t.Errorf("%s required an unrelated Grafana binding: %v", test.name, err)
		}
	}
}

func TestPersonalGrafanaBindingRejectsOtherOwner(t *testing.T) {
	owner := "personal-11111111-1111-4111-8111-111111111111"
	other := "personal-22222222-2222-4222-8222-222222222222"
	items := []any{
		map[string]any{"spec": map[string]any{"tenantRef": other, "serviceClass": "observability.grafana.v1"},
			"status": map[string]any{"phase": "Ready", "access": map[string]any{"organizationId": float64(9)}, "humanCredentialRef": "credential://grafana/" + other + "/other"}},
		map[string]any{"spec": map[string]any{"tenantRef": owner, "serviceClass": "observability.grafana.v1"},
			"status": map[string]any{"phase": "Ready", "access": map[string]any{"organizationId": float64(11)}, "humanCredentialRef": "credential://grafana/" + owner + "/own"}},
	}
	orgID, reference, err := personalGrafanaAccess(owner, items)
	if err != nil || orgID != 11 || reference != "credential://grafana/"+owner+"/own" {
		t.Fatalf("Personal Grafana binding crossed owner boundary: %d %s %v", orgID, reference, err)
	}
	if _, _, err := personalGrafanaAccess("personal-33333333-3333-4333-8333-333333333333", items); err == nil {
		t.Fatal("unprovisioned Personal organization inherited another Grafana binding")
	}
}

func TestGrafanaQueryTenantBoundary(t *testing.T) {
	binding := grafanaBinding{TenantLabel: "acme", OrgID: 9}
	datasources := []map[string]any{{"uid": "metrics", "type": "prometheus"}}
	spec := map[string]any{"queries": []any{map[string]any{"datasourceUid": "metrics", "refId": "A", "queryKind": "promql", "model": map[string]any{"expr": `rate(http_requests_total{tenant="other"}[5m])`}}}}
	if _, _, err := validateGrafanaSpec(spec, binding, datasources); err == nil {
		t.Fatal("query without authenticated tenant matcher accepted")
	}
	spec["queries"] = []any{map[string]any{"datasourceUid": "metrics", "refId": "A", "queryKind": "promql", "model": map[string]any{"expr": `rate(http_requests_total{tenant="acme"}[5m])`}}}
	if _, _, err := validateGrafanaSpec(spec, binding, datasources); err != nil {
		t.Fatal(err)
	}
	spec["queries"] = []any{map[string]any{"datasourceUid": "metrics", "refId": "A", "queryKind": "promql", "model": map[string]any{"expr": `http_requests_total{tenant="acme"} or errors_total`}}}
	if _, _, err := validateGrafanaSpec(spec, binding, datasources); err == nil {
		t.Fatal("unscoped or branch accepted")
	}
	spec["queries"] = []any{map[string]any{"datasourceUid": "metrics", "refId": "A", "queryKind": "sql", "model": map[string]any{"expr": `http_requests_total{tenant="acme"}`}}}
	if _, _, err := validateGrafanaSpec(spec, binding, datasources); err == nil {
		t.Fatal("datasource query kind mismatch accepted")
	}
	if err := validateSQL("SELECT * FROM usage; DROP TABLE usage"); err == nil {
		t.Fatal("multiple SQL statements accepted")
	}
}

func TestMetricsQLEverySelectorBranchIsTenantScoped(t *testing.T) {
	for _, query := range []string{
		`sum(rate(http_requests_total{tenant="acme"}[5m]))`,
		`http_requests_total{tenant="acme"} or errors_total{tenant="acme"}`,
		`http_requests_total{tenant="acme",job="api" or tenant="acme",job="worker"}`,
	} {
		if err := validateMetricsTenantScope(query, "acme"); err != nil {
			t.Errorf("scoped query rejected %q: %v", query, err)
		}
	}
	for _, query := range []string{
		`http_requests_total`,
		`http_requests_total{tenant="acme"} or errors_total`,
		`http_requests_total{tenant="acme",job="api" or tenant="other",job="worker"}`,
		`http_requests_total{tenant=~"acme|other"}`,
		`http_requests_total{tenant!="other"}`,
		`http_requests_total{tenant="acme"} + errors_total{job="api"}`,
		`vector(1)`,
	} {
		if err := validateMetricsTenantScope(query, "acme"); err == nil {
			t.Errorf("unscoped query accepted: %q", query)
		}
	}
}

func TestDashboardRenderAndSaveTenantScope(t *testing.T) {
	datasources := []map[string]any{{"uid": "metrics", "type": "prometheus"}, {"uid": "logs", "type": "loki"}}
	dashboard := map[string]any{"panels": []any{map[string]any{
		"id": 1, "type": "timeseries", "targets": []any{map[string]any{
			"refId": "A", "datasource": map[string]any{"uid": "metrics"}, "expr": `http_requests_total{tenant="acme"}`,
		}},
	}}}
	if err := validateGrafanaDashboardScope(dashboard, "acme", datasources); err != nil {
		t.Fatal(err)
	}
	target := slice(object(slice(dashboard["panels"])[0])["targets"])[0].(map[string]any)
	for _, expr := range []string{`http_requests_total`, `http_requests_total{tenant="acme"} or errors_total`, `http_requests_total{tenant="other"}`} {
		target["expr"] = expr
		if err := validateGrafanaDashboardScope(dashboard, "acme", datasources); err == nil {
			t.Errorf("unscoped dashboard target accepted: %s", expr)
		}
	}
	target["expr"] = `http_requests_total{tenant="acme"}`
	target["datasource"] = map[string]any{"uid": "logs"}
	if err := validateGrafanaDashboardScope(dashboard, "acme", datasources); err == nil {
		t.Fatal("unsupported log datasource accepted")
	}
	target["datasource"] = map[string]any{"uid": "metrics"}
	dashboard["templating"] = map[string]any{"list": []any{map[string]any{"query": "label_values(up,tenant)"}}}
	if err := validateGrafanaDashboardScope(dashboard, "acme", datasources); err == nil {
		t.Fatal("query variable accepted")
	}
	delete(dashboard, "templating")
	panel := object(slice(dashboard["panels"])[0])
	panel["type"] = "custom-plugin"
	if err := validateGrafanaDashboardScope(dashboard, "acme", datasources); err == nil {
		t.Fatal("unreviewed panel plugin accepted")
	}
}

func TestGrafanaAnalysisHandlesLongLag(t *testing.T) {
	rows := lagCorrelations([]float64{1, 2}, []float64{2, 4}, "pearson", 100)
	if len(rows) != 201 {
		t.Fatalf("wrong lag count: %d", len(rows))
	}
	result, err := indicators([]float64{1, 2, 3}, []any{map[string]any{"type": "movingAverage", "window": float64(2)}})
	if err != nil {
		t.Fatal(err)
	}
	values := result["movingAverage"].([]float64)
	if values[2] != 2.5 {
		t.Fatalf("moving average = %v", values)
	}
}
