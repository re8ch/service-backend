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
	if err := validateSQL("SELECT * FROM usage; DROP TABLE usage"); err == nil {
		t.Fatal("multiple SQL statements accepted")
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
