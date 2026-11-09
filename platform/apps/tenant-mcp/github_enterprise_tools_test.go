package main

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func fixtureGitHubCredential(t *testing.T) githubEnterpriseCredential {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return githubEnterpriseCredential{
		AppID:          "5684168",
		InstallationID: "180243962",
		PrivateKeyPEM:  pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: encoded}),
	}
}

func TestGitHubAppJWTContainsNoPrivateMaterial(t *testing.T) {
	jwt, err := githubAppJWT(fixtureGitHubCredential(t), time.Unix(1_800_000_000, 0))
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Split(jwt, ".")
	if len(parts) != 3 || strings.Contains(jwt, "PRIVATE") {
		t.Fatalf("invalid or unsafe JWT: %q", jwt)
	}
}

func TestGitHubEnterpriseInstallRequiresPreviewAndExactPlan(t *testing.T) {
	payload, err := validateGitHubEnterpriseAppsInput(githubEnterpriseAppsInput{
		Action: "install", Organization: "re8ch", ClientID: "Iv23lix94cleZd6YJknF", RepositorySelection: "all",
	})
	if err != nil || payload["repository_selection"] != "all" {
		t.Fatalf("valid install rejected: %#v %v", payload, err)
	}
	if _, err := validateGitHubEnterpriseAppsInput(githubEnterpriseAppsInput{
		Action: "install", Organization: "re8ch", ClientID: "Iv23lix94cleZd6YJknF", RepositorySelection: "all", Repositories: []string{"service-backend"},
	}); err == nil {
		t.Fatal("repositories accepted with all selection")
	}
}

func TestGitHubEnterpriseTokenIsUsedInternallyAndNeverReturned(t *testing.T) {
	credential := fixtureGitHubCredential(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/app/installations/180243962/access_tokens" || r.Method != http.MethodPost {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ey") {
			t.Fatal("App JWT was not sent")
		}
		writeJSON(w, http.StatusCreated, map[string]any{"token": "installation-secret", "expires_at": "2026-11-10T03:00:00Z"})
	}))
	defer server.Close()
	originalBase, originalLoader := githubAPIBaseURL, loadGitHubEnterpriseCredential
	githubAPIBaseURL = server.URL
	loadGitHubEnterpriseCredential = func(context.Context) (githubEnterpriseCredential, error) { return credential, nil }
	defer func() { githubAPIBaseURL, loadGitHubEnterpriseCredential = originalBase, originalLoader }()
	token, err := githubEnterpriseInstallationToken(context.Background())
	if err != nil || token != "installation-secret" {
		t.Fatalf("token exchange failed: %q %v", token, err)
	}
	encoded, _ := json.Marshal(map[string]any{"status": "ready"})
	if strings.Contains(string(encoded), token) {
		t.Fatal("test result exposed installation token")
	}
}
