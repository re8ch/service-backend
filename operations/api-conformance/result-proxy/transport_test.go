package resultproxy

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestJWKSCacheAndFailure(t *testing.T) {
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	jwk := map[string]string{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "key", "n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()), "e": "AQAB"}
	body, _ := json.Marshal(map[string]any{"keys": []any{jwk}})
	calls := 0
	status := 200
	g := NewGitHubKeys()
	g.client.Transport = roundTrip(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.URL.String() != jwksURL || r.Header.Get("Authorization") != "" {
			t.Fatal("untrusted key request")
		}
		return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(string(body))), Header: make(http.Header)}, nil
	})
	for i := 0; i < 2; i++ {
		keys, err := g.Keys(context.Background())
		if err != nil || keys["key"].E != 65537 {
			t.Fatal("valid JWKS rejected")
		}
	}
	if calls != 1 {
		t.Fatal("cache ignored")
	}
	g.expires = time.Time{}
	g.retry = time.Time{}
	status = 503
	if _, err := g.Keys(context.Background()); err == nil {
		t.Fatal("stale key accepted")
	}
	if _, err := g.Keys(context.Background()); err == nil || calls != 2 {
		t.Fatal("failure not throttled")
	}
	for _, bad := range []string{`{}`, `{"keys":[]}`, `{"keys":[{"kty":"EC"}]}`, strings.Replace(string(body), "AQAB", "Ag", 1)} {
		if _, err := parseKeys([]byte(bad)); err == nil {
			t.Fatal("invalid key accepted")
		}
	}
	dup, _ := json.Marshal(map[string]any{"keys": []any{jwk, jwk}})
	if _, err := parseKeys(dup); err == nil {
		t.Fatal("duplicate key accepted")
	}
	if NewGitHubKeys().client.CheckRedirect(nil, nil) == nil {
		t.Fatal("redirect allowed")
	}
}

func TestAPITransportTLSRotationAndBoundaries(t *testing.T) {
	wanted := "first"
	redirect := false
	oversized := false
	calls := 0
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer "+wanted {
			t.Error("stale token")
		}
		if redirect {
			w.Header().Set("Location", "https://evil.invalid")
			w.WriteHeader(302)
			return
		}
		if oversized {
			io.WriteString(w, strings.Repeat("x", 65537))
			return
		}
		io.WriteString(w, `{}`)
	}))
	defer srv.Close()
	dir := t.TempDir()
	ca := filepath.Join(dir, "ca.pem")
	token := filepath.Join(dir, "token")
	if err := os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: srv.Certificate().Raw}), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(token, []byte(wanted), 0600); err != nil {
		t.Fatal(err)
	}
	a, err := NewAPITransport(srv.URL, ca, token)
	if err != nil {
		t.Fatal(err)
	}
	path := "/apis/conformance.re8ch.com/v1alpha1/namespaces/test/apiconformanceruns/run-1"
	request := func() error {
		_, err := a.Request(context.Background(), "GET", path, "application/json", nil)
		return err
	}
	if err := request(); err != nil {
		t.Fatal(err)
	}
	wanted = "rotated"
	if err := os.WriteFile(token, []byte(wanted), 0600); err != nil {
		t.Fatal(err)
	}
	if err := request(); err != nil {
		t.Fatal(err)
	}
	before := calls
	for _, bad := range []string{"/api/v1/secrets", path + "?token=x", path + "/../../secrets"} {
		if _, err := a.Request(context.Background(), "GET", bad, "application/json", nil); err == nil {
			t.Fatal("unsafe path")
		}
	}
	if calls != before {
		t.Fatal("unsafe path sent upstream")
	}
	redirect = true
	if request() == nil {
		t.Fatal("redirect allowed")
	}
	redirect = false
	oversized = true
	if request() == nil {
		t.Fatal("oversized response allowed")
	}
	if _, err := NewAPITransport("http://example.com", ca, token); err == nil {
		t.Fatal("plaintext allowed")
	}
	// A system trust store must reject the test server's untrusted CA.
	a.client = &http.Client{Timeout: time.Second}
	if request() == nil {
		t.Fatal("invalid TLS accepted")
	}
}
