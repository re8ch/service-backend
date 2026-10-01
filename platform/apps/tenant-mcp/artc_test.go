package main

import "testing"

func TestArtcGuardDeniesCredentialExport(t *testing.T) {
	for _, argv := range [][]string{{"get", "secrets"}, {"exec", "pod/app", "--", "sh"}, {"get", "pods", "--kubeconfig=/tmp/other"}, {"auth", "can-i", "get", "pods"}} {
		if !forbiddenAdminArgv(argv, "") {
			t.Errorf("unsafe argv accepted: %v", argv)
		}
	}
	if !forbiddenAdminArgv([]string{"apply", "-f", "-"}, "kind: Secret\nmetadata: {}") {
		t.Fatal("inline Secret accepted")
	}
	if forbiddenAdminArgv([]string{"get", "pods", "-n", "artchais"}, "") {
		t.Fatal("safe read denied")
	}
	if !containsSensitiveField(map[string]any{"spec": map[string]any{"apiKey": "secret"}}) {
		t.Fatal("sensitive field accepted")
	}
}
