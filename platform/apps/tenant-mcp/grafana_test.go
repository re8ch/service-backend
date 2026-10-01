package main

import (
	"testing"
)

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
