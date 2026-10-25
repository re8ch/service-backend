package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestRuntimeImageOperationRoutesAndValidatesActions(t *testing.T) {
	tests := []struct {
		name      string
		args      map[string]any
		operation string
		wantError bool
	}{
		{name: "runtime_image_catalog", args: map[string]any{"action": "list_repositories"}, operation: "harbor_repository_list"},
		{name: "runtime_image_catalog", args: map[string]any{"action": "get_sbom", "repository": "app", "reference": "sha256:abc"}, operation: "harbor_sbom_get"},
		{name: "runtime_image_catalog", args: map[string]any{"action": "get_artifact", "repository": "app"}, wantError: true},
		{name: "runtime_image_manage", args: map[string]any{"action": "delete_tag", "repository": "app", "reference": "sha256:abc", "tag": "old"}, operation: "harbor_tag_delete"},
		{name: "runtime_image_manage", args: map[string]any{"action": "create_tag", "repository": "app", "reference": "sha256:abc"}, wantError: true},
		{name: "runtime_image_build", args: map[string]any{"action": "get", "operation_id": "op-1"}, operation: "harbor_build_get"},
		{name: "runtime_image_build", args: map[string]any{"action": "start"}, wantError: true},
		{name: "runtime_image_build", args: map[string]any{"action": "unknown"}, wantError: true},
	}
	for _, test := range tests {
		operation, err := runtimeImageOperation(test.name, test.args)
		if test.wantError {
			if err == nil {
				t.Errorf("%s/%v unexpectedly routed to %s", test.name, test.args, operation)
			}
			continue
		}
		if err != nil || operation != test.operation {
			t.Errorf("%s/%v = %s/%v, want %s", test.name, test.args, operation, err, test.operation)
		}
	}
}

func TestHarborPathAndSecretRedaction(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.EscapedPath() != "/api/v2.0/projects/acme/repositories/group%2Fapp/artifacts" {
			t.Errorf("unexpected Harbor path %s", r.URL.EscapedPath())
		}
		if username, password, ok := r.BasicAuth(); !ok || username != "robot" || password != "secret" {
			t.Error("Harbor credential missing")
		}
		_ = json.NewEncoder(w).Encode([]any{map[string]any{"name": "app", "secret": "credential"}})
	}))
	defer server.Close()
	client := &harborClient{base: server.URL + "/api/v2.0", username: "robot", password: "secret", http: &http.Client{Timeout: time.Second}}
	items, err := client.paged(context.Background(), "/projects/acme/repositories/group%2Fapp/artifacts")
	if err != nil || len(items) != 1 {
		t.Fatalf("Harbor page: %v %v", items, err)
	}
	if object(items[0])["secret"] != "[REDACTED]" {
		t.Fatal("Harbor secret was not redacted")
	}
}
