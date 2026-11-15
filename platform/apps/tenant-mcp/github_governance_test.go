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
	for resource := range map[string]bool{"repository_ruleset": true, "organization_ruleset": true, "repository_variable": true, "organization_variable": true, "organization": true} {
		if _, _, _, err := githubGovernanceRequest(githubGovernanceInput{Resource: resource, Operation: "delete", Owner: "re8ch", Repo: "service-backend", Name: "X", ID: 1}); err == nil || !strings.Contains(err.Error(), "not available") {
			t.Fatalf("delete accepted for %s: %v", resource, err)
		}
	}
}
