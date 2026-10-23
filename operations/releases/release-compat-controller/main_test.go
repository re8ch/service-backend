package main

import (
	"testing"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

func TestOperationExpiryUsesCreationTimestampAndTimeout(t *testing.T) {
	created := time.Date(2026, 10, 24, 0, 0, 0, 0, time.UTC)
	operation := &unstructured.Unstructured{Object: map[string]any{
		"spec": map[string]any{"timeoutSeconds": int64(1800)},
	}}
	operation.SetCreationTimestamp(metav1.NewTime(created))
	if operationExpired(operation, created.Add(29*time.Minute)) {
		t.Fatal("operation expired before its timeout")
	}
	if !operationExpired(operation, created.Add(31*time.Minute)) {
		t.Fatal("operation remained dispatchable after its timeout")
	}
}

func TestOperationNameIsStableAndActionScoped(t *testing.T) {
	a := operationName("rook-ceph", "reconcile", "123")
	if a != operationName("rook-ceph", "reconcile", "123") || a == operationName("rook-ceph", "force-reconcile", "123") || len(a) > 63 {
		t.Fatal("operation idempotency identity is invalid")
	}
}

func TestOperationNameDoesNotTrustFluxHandledStatus(t *testing.T) {
	requestedAt := "2026-10-23T21:05:43.284Z"
	// A retiring Flux controller can copy requestedAt into
	// lastHandledReconcileAt before the compatibility controller observes it.
	// Operation identity must remain derived from the request itself.
	got := operationName("rook-ceph", "reconcile", requestedAt)
	want := "rook-ceph-9cd3f4260b72b501"
	if got != want {
		t.Fatalf("unexpected operation identity: got %q want %q", got, want)
	}
}

func TestConditionProjection(t *testing.T) {
	status := map[string]any{"conditions": []any{map[string]any{"type": "Healthy", "status": "False", "reason": "Frozen"}}}
	value, reason := condition(status, "Healthy")
	if value != "False" || reason != "Frozen" {
		t.Fatal(value, reason)
	}
}

func TestProjectedHistoryIsHelmReleaseSchemaCompatible(t *testing.T) {
	release := &unstructured.Unstructured{Object: map[string]any{
		"metadata": map[string]any{"name": "rook-ceph", "namespace": "flux-system", "creationTimestamp": "2026-10-23T00:00:00Z"},
		"spec":     map[string]any{"valuesDigest": "sha256:values", "bundleDigest": "sha256:bundle"},
		"status":   map[string]any{"history": []any{map[string]any{"chartName": "rook-ceph", "digest": "sha256:old", "status": "deployed", "version": int64(3)}}},
	}}
	release.SetCreationTimestamp(metav1.Date(2026, 10, 23, 0, 0, 0, 0, metav1.Now().Location()))
	helm := &unstructured.Unstructured{Object: map[string]any{"spec": map[string]any{
		"releaseName": "rook-ceph", "targetNamespace": "rook-ceph",
		"chart": map[string]any{"spec": map[string]any{"version": "v1.20.3"}},
	}}}
	history := projectedHistory(release, helm, "2026-10-24T00:00:00Z")
	if len(history) != 1 {
		t.Fatalf("expected one history entry, got %d", len(history))
	}
	entry := history[0].(map[string]any)
	required := []string{"chartName", "chartVersion", "configDigest", "digest", "firstDeployed", "lastDeployed", "name", "namespace", "status", "version"}
	for _, field := range required {
		if entry[field] == nil || entry[field] == "" {
			t.Fatalf("required HelmRelease history field %q is empty: %#v", field, entry)
		}
	}
	if entry["namespace"] != "rook-ceph" || entry["digest"] != "sha256:old" || entry["ociDigest"] != "sha256:bundle" {
		t.Fatalf("unexpected projected history: %#v", entry)
	}
}
