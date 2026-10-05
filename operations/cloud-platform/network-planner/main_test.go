package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestPlanCreateBindsPathAndMarker(t *testing.T) {
	op := Operation{Name: "test", Method: "POST", Path: "/apis/networking.advfab.org/v1alpha1/networkinterventions", GroupVersion: "apis/networking.advfab.org/v1alpha1", Kind: "NetworkIntervention", SchemaHash: strings.Repeat("a", 64)}
	body := json.RawMessage(`{"apiVersion":"networking.advfab.org/v1alpha1","kind":"NetworkIntervention","metadata":{"name":"tool-canary"},"spec":{}}`)
	result, err := plan(op, Input{Name: "tool-canary", IdempotencyKey: "request-123", Body: body})
	if err != nil {
		t.Fatal(err)
	}
	if result.Path != op.Path || result.Method != "POST" || len(result.PlanHash) != 64 {
		t.Fatalf("bad bound plan: %#v", result)
	}
	var parsed map[string]any
	json.Unmarshal(result.Body, &parsed)
	metadata := parsed["metadata"].(map[string]any)
	marker := metadata["annotations"].(map[string]any)["operations.re8ch.com/idempotency-hash"].(string)
	if len(marker) != 64 {
		t.Fatalf("missing marker: %v", metadata)
	}
}

func TestPlanRejectsTargetChangesAndRequiresPreconditions(t *testing.T) {
	op := Operation{Name: "test", Method: "PUT", Path: "/apis/networking.advfab.org/v1alpha1/networkinterventions/{name}", GroupVersion: "apis/networking.advfab.org/v1alpha1", Kind: "NetworkIntervention", SchemaHash: strings.Repeat("a", 64)}
	existing := json.RawMessage(`{"apiVersion":"networking.advfab.org/v1alpha1","kind":"NetworkIntervention","metadata":{"name":"tool-canary","uid":"uid-1","resourceVersion":"7"}}`)
	body := json.RawMessage(`{"apiVersion":"networking.advfab.org/v1alpha1","kind":"NetworkIntervention","metadata":{"name":"tool-canary","uid":"uid-1","resourceVersion":"6"},"spec":{}}`)
	_, err := plan(op, Input{Name: "tool-canary", IdempotencyKey: "request-123", Body: body, Existing: existing})
	if err == nil {
		t.Fatal("stale resourceVersion accepted")
	}
	_, err = plan(op, Input{Name: "bad/name", IdempotencyKey: "request-123", Body: body, Existing: existing})
	if err == nil {
		t.Fatal("unbound path accepted")
	}
}

func TestDeleteUsesUIDAndResourceVersion(t *testing.T) {
	op := Operation{Name: "test", Method: "DELETE", Path: "/apis/networking.advfab.org/v1alpha1/networkinterventions/{name}", GroupVersion: "apis/networking.advfab.org/v1alpha1", Kind: "NetworkIntervention", SchemaHash: strings.Repeat("a", 64)}
	existing := json.RawMessage(`{"apiVersion":"networking.advfab.org/v1alpha1","kind":"NetworkIntervention","metadata":{"name":"tool-canary","uid":"uid-1","resourceVersion":"7"}}`)
	result, err := plan(op, Input{Name: "tool-canary", IdempotencyKey: "request-123", Existing: existing})
	if err != nil {
		t.Fatal(err)
	}
	var options struct {
		Preconditions struct {
			UID             string `json:"uid"`
			ResourceVersion string `json:"resourceVersion"`
		} `json:"preconditions"`
	}
	json.Unmarshal(result.Body, &options)
	if options.Preconditions.UID != "uid-1" || options.Preconditions.ResourceVersion != "7" {
		t.Fatalf("bad preconditions: %s", result.Body)
	}
}
