package main

import (
	"net/http"
	"strings"
	"testing"
)

func TestGitHubGovernanceRequestAllowlist(t *testing.T) {
	method, path, _, err := githubGovernanceRequest(githubGovernanceInput{Resource: "repository_ruleset", Operation: "list", Owner: "re8ch", Repo: "service-backend"})
	if err != nil || method != http.MethodGet || path != "/repos/re8ch/service-backend/rulesets" {
		t.Fatalf("unexpected ruleset request: %s %s %v", method, path, err)
	}
	method, path, _, err = githubGovernanceRequest(githubGovernanceInput{Resource: "organization_variable", Operation: "update", Owner: "siren-observer", Name: "RELEASE_MODE", Body: map[string]any{"value": "protected"}})
	if err != nil || method != http.MethodPatch || path != "/orgs/siren-observer/actions/variables/RELEASE_MODE" {
		t.Fatalf("unexpected variable request: %s %s %v", method, path, err)
	}
	for _, input := range []githubGovernanceInput{
		{Resource: "repository_ruleset", Operation: "list", Owner: "../outside", Repo: "repo"},
		{Resource: "repository_ruleset", Operation: "delete", Owner: "re8ch", Repo: "service-backend"},
		{Resource: "repository_variable", Operation: "get", Owner: "re8ch", Repo: "service-backend", Name: "../secret"},
		{Resource: "raw_api", Operation: "call", Owner: "re8ch"},
		{Resource: "organization", Operation: "update", Owner: "re8ch", Body: map[string]any{"members_can_delete_repositories": true}},
	} {
		if _, _, _, err := githubGovernanceRequest(input); err == nil {
			t.Fatalf("unsafe governance input accepted: %#v", input)
		}
	}
	method, path, _, err = githubGovernanceRequest(githubGovernanceInput{Resource: "organization", Operation: "get", Owner: "thinking-machine-labs"})
	if err != nil || method != http.MethodGet || path != "/orgs/thinking-machine-labs" {
		t.Fatalf("enterprise organization request was rejected: %s %s %v", method, path, err)
	}
	method, path, body, err := githubGovernanceRequest(githubGovernanceInput{Resource: "workflow", Operation: "dispatch", Owner: "strata-silica", Repo: "storage-gitops", Name: "storage-reconcile.yml", Body: map[string]any{"ref": "main"}})
	if err != nil || method != http.MethodPost || path != "/repos/strata-silica/storage-gitops/actions/workflows/storage-reconcile.yml/dispatches" || !strings.Contains(string(body), `"ref":"main"`) {
		t.Fatalf("unexpected workflow dispatch request: %s %s %s %v", method, path, body, err)
	}
	method, path, _, err = githubGovernanceRequest(githubGovernanceInput{Resource: "workflow_run", Operation: "rerun_failed", Owner: "strata-silica", Repo: "storage-gitops", ID: 43699449394})
	if err != nil || method != http.MethodPost || path != "/repos/strata-silica/storage-gitops/actions/runs/43699449394/rerun-failed-jobs" {
		t.Fatalf("unexpected workflow rerun request: %s %s %v", method, path, err)
	}
	method, path, body, err = githubGovernanceRequest(githubGovernanceInput{Resource: "organization_custom_property_schema", Operation: "update", Owner: "re8ch", Body: map[string]any{"properties": []any{map[string]any{"property_name": "ops.repository-role", "value_type": "single_select", "required": true, "allowed_values": []any{"product", "gitops", "control-plane"}}}}})
	if err != nil || method != http.MethodPatch || path != "/orgs/re8ch/properties/schema" || !strings.Contains(string(body), "ops.repository-role") {
		t.Fatalf("unexpected custom property schema request: %s %s %s %v", method, path, body, err)
	}
	method, path, body, err = githubGovernanceRequest(githubGovernanceInput{Resource: "repository_custom_property_values", Operation: "update", Owner: "re8ch", Repo: "service-backend", Body: map[string]any{"properties": []any{map[string]any{"property_name": "ops.repository-role", "value": "control-plane"}}}})
	if err != nil || method != http.MethodPatch || path != "/repos/re8ch/service-backend/properties/values" || !strings.Contains(string(body), "control-plane") {
		t.Fatalf("unexpected repository custom property request: %s %s %s %v", method, path, body, err)
	}
}

func TestGitHubGovernanceRejectsUnsafeCustomProperties(t *testing.T) {
	for _, input := range []githubGovernanceInput{
		{Resource: "organization_custom_property_schema", Operation: "update", Owner: "re8ch", Body: map[string]any{"properties": []any{map[string]any{"property_name": "risk", "value_type": "secret"}}}},
		{Resource: "organization_custom_property_schema", Operation: "update", Owner: "re8ch", Body: map[string]any{"properties": []any{map[string]any{"property_name": "risk", "value_type": "string", "private_key": "x"}}}},
		{Resource: "repository_custom_property_values", Operation: "update", Owner: "re8ch", Repo: "service-backend", Body: map[string]any{"properties": []any{map[string]any{"property_name": "risk"}}}},
	} {
		if _, _, _, err := githubGovernanceRequest(input); err == nil {
			t.Fatalf("unsafe custom property input accepted: %#v", input)
		}
	}
}

func TestGitHubGovernanceOrganizationUpdateFields(t *testing.T) {
	method, path, body, err := githubGovernanceRequest(githubGovernanceInput{
		Resource:  "organization",
		Operation: "update",
		Owner:     "re8ch",
		Body: map[string]any{
			"default_repository_permission": "read",
			"web_commit_signoff_required":   true,
		},
	})
	if err != nil || method != http.MethodPatch || path != "/orgs/re8ch" {
		t.Fatalf("unexpected organization update request: %s %s %v", method, path, err)
	}
	if !strings.Contains(string(body), "default_repository_permission") {
		t.Fatalf("organization update body was not preserved: %s", body)
	}
}

func TestGitHubGovernanceCredentialFailsClosed(t *testing.T) {
	t.Setenv("GITHUB_GOVERNANCE_APP_ID", "")
	t.Setenv("GITHUB_GOVERNANCE_PRIVATE_KEY", "")
	if _, err := githubGovernanceJWT("governance"); err == nil || !strings.Contains(err.Error(), "not configured") {
		t.Fatalf("missing credential accepted: %v", err)
	}
	if _, err := githubGovernanceJWT("unknown"); err == nil || !strings.Contains(err.Error(), "unsupported") {
		t.Fatalf("unknown capability accepted: %v", err)
	}
}

func TestGitHubGovernanceRejectsEveryDeleteOperation(t *testing.T) {
	for resource := range map[string]bool{"repository_ruleset": true, "organization_ruleset": true, "repository_variable": true, "organization_variable": true, "organization_custom_property_schema": true, "repository_custom_property_values": true, "organization": true, "workflow": true, "workflow_run": true} {
		if _, _, _, err := githubGovernanceRequest(githubGovernanceInput{Resource: resource, Operation: "delete", Owner: "re8ch", Repo: "service-backend", Name: "X", ID: 1}); err == nil || !strings.Contains(err.Error(), "not available") {
			t.Fatalf("delete accepted for %s: %v", resource, err)
		}
	}
}
