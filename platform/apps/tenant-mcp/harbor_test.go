package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

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
