package main

import "testing"

func TestOperationNameIsStableAndActionScoped(t *testing.T) {
	a := operationName("rook-ceph", "reconcile", "123")
	if a != operationName("rook-ceph", "reconcile", "123") || a == operationName("rook-ceph", "force-reconcile", "123") || len(a) > 63 {
		t.Fatal("operation idempotency identity is invalid")
	}
}

func TestConditionProjection(t *testing.T) {
	status := map[string]any{"conditions": []any{map[string]any{"type": "Healthy", "status": "False", "reason": "Frozen"}}}
	value, reason := condition(status, "Healthy")
	if value != "False" || reason != "Frozen" {
		t.Fatal(value, reason)
	}
}
