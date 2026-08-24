package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestMutatePodAddsR640Boundary(t *testing.T) {
	patch, err := mutatePod(json.RawMessage(`{"spec":{"containers":[{"name":"web","securityContext":{"allowPrivilegeEscalation":false}}]}}`))
	if err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(patch)
	for _, wanted := range []string{"kubernetes.io/hostname", "r640", "pg-re8ch"} {
		if !strings.Contains(string(data), wanted) {
			t.Fatalf("patch missing %q: %s", wanted, data)
		}
	}
}

func TestMutatePodRejectsUnsafeSettings(t *testing.T) {
	tests := []string{
		`{"spec":{"hostNetwork":true,"containers":[{"name":"x"}]}}`,
		`{"spec":{"volumes":[{"name":"x","hostPath":{"path":"/"}}],"containers":[{"name":"x"}]}}`,
		`{"spec":{"containers":[{"name":"x","securityContext":{"privileged":true}}]}}`,
		`{"spec":{"containers":[{"name":"x","securityContext":{"capabilities":{"add":["SYS_ADMIN"]}}}]}}`,
	}
	for _, input := range tests {
		if _, err := mutatePod(json.RawMessage(input)); err == nil {
			t.Fatalf("expected rejection for %s", input)
		}
	}
}
