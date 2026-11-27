package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestCollaborationBrokerBindsInvocationOwnerScopesAndConnection(t *testing.T) {
	s := testServer(t)
	ref := "collaboration://0123456789abcdef0123456789abcdef"
	expires := time.Now().Add(10 * time.Minute).Unix()
	if _, err := s.db.Exec(`INSERT INTO collaboration_connections(ref,tenant,subject,request_hash,scopes,state,account_id,expires) VALUES(?,?,?,?,?,'Ready','account-1',?)`, ref, "re8ch", "owner", "hash", `["mail.read","mail.send","files.read"]`, expires); err != nil {
		t.Fatal(err)
	}
	t.Setenv("COLLABORATION_ENABLED", "true")
	t.Setenv("STALWART_URL", "https://stalwart.example")
	t.Setenv("STALWART_TOOL_REF", "tool://8iops/stalwart-agent-tools")
	t.Setenv("STALWART_TOOL_OWNER_ORG_REF", "publisher")
	t.Setenv("STALWART_TOOL_AUDIENCE", "re8ch-tenant-tool")
	authority := testInvocationIssuer(t)
	t.Setenv("INVOCATION_TOKEN_ISSUER", authority.issuer)
	t.Setenv("INVOCATION_TOKEN_JWKS_URL", authority.jwksURL)
	oldBroker := collaborationBrokerRequest
	oldWorkloadAuth := collaborationBrokerWorkloadAuthenticated
	collaborationBrokerWorkloadAuthenticated = func(*http.Request) (bool, error) { return true, nil }
	collaborationBrokerRequest = func(_ context.Context, method, _ string, _ any) (map[string]any, error) {
		if method != http.MethodGet {
			t.Fatalf("unexpected broker method %s", method)
		}
		encoded := func(value string) string { return base64.StdEncoding.EncodeToString([]byte(value)) }
		return map[string]any{"metadata": map[string]any{"labels": map[string]any{"credentials.re8ch.com/tenant": "re8ch", "credentials.re8ch.com/subject": tokenDigest("owner")[:32], "credentials.re8ch.com/provider": "stalwart"}}, "data": map[string]any{"tenant": encoded("re8ch"), "subject": encoded("owner"), "origin": encoded("https://stalwart.example"), "accountID": encoded("account-1"), "accessToken": encoded("provider-token")}}, nil
	}
	t.Cleanup(func() {
		collaborationBrokerRequest = oldBroker
		collaborationBrokerWorkloadAuthenticated = oldWorkloadAuth
	})

	issue := func(subject, binding string, scopes []string) string {
		t.Helper()
		issued, err := authority.Issue(context.Background(), invocationTokenRequest{Subject: subject, ActiveOrgRef: "re8ch", ToolRef: "tool://8iops/stalwart-agent-tools", BindingRef: binding, Scopes: scopes, Audience: "re8ch-tenant-tool"})
		if err != nil {
			t.Fatal(err)
		}
		return issued.Token
	}
	call := func(token string, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, "/v1/collaboration/resolve", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer workload-token")
		req.Header.Set("X-RE8CH-Invocation", "Bearer "+token)
		response := httptest.NewRecorder()
		s.collaborationBrokerResolveHTTP(response, req)
		return response
	}
	body := `{"connectionRef":"` + ref + `","requiredScopes":["mail.send"]}`
	response := call(issue("owner", ref, []string{"mail.send"}), body)
	if response.Code != http.StatusOK {
		t.Fatalf("owner resolution failed: %d %s", response.Code, response.Body.String())
	}
	var resolved map[string]any
	if json.Unmarshal(response.Body.Bytes(), &resolved) != nil || resolved["accountId"] != "account-1" || resolved["bearerToken"] != "provider-token" {
		t.Fatalf("invalid broker result: %s", response.Body.String())
	}
	for name, token := range map[string]string{
		"wrong owner":   issue("other", ref, []string{"mail.send"}),
		"wrong binding": issue("owner", "collaboration://ffffffffffffffffffffffffffffffff", []string{"mail.send"}),
		"wrong scope":   issue("owner", ref, []string{"mail.read"}),
	} {
		t.Run(name, func(t *testing.T) {
			if got := call(token, body); got.Code != http.StatusForbidden {
				t.Fatalf("boundary bypassed: %d %s", got.Code, got.Body.String())
			}
		})
	}
}

func TestCollaborationBrokerRejectsMissingWorkloadIdentity(t *testing.T) {
	s := testServer(t)
	req := httptest.NewRequest(http.MethodPost, "/v1/collaboration/resolve", strings.NewReader(`{}`))
	response := httptest.NewRecorder()
	s.collaborationBrokerResolveHTTP(response, req)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("missing workload identity accepted: %d", response.Code)
	}
}
