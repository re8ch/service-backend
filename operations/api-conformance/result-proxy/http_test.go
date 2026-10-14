package resultproxy

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type staticKeys map[string]*rsa.PublicKey

func (s staticKeys) Keys(context.Context) (map[string]*rsa.PublicKey, error) { return s, nil }

func TestHTTPRedemption(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	p := Policy{Audience: "reports", Repository: "org/repo", RepositoryID: "42", OwnerID: "7", Ref: "refs/heads/main", WorkflowRef: "org/repo/.github/workflows/results.yml@refs/heads/main", WorkflowSHA: strings.Repeat("a", 40)}
	c := Claims{Issuer: Issuer, Audience: p.Audience, Subject: "repo:org/repo:ref:refs/heads/main", Repository: p.Repository, RepositoryID: p.RepositoryID, OwnerID: p.OwnerID, Ref: p.Ref, WorkflowRef: p.WorkflowRef, WorkflowSHA: p.WorkflowSHA, Event: "workflow_dispatch", RunID: "123", RunAttempt: "1", JTI: "unique", Issued: now.Unix(), NotBefore: now.Unix(), Expires: now.Unix() + 300}
	body, _ := json.Marshal(c)
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"RS256","kid":"key","typ":"JWT"}`))
	unsigned := header + "." + base64.RawURLEncoding.EncodeToString(body)
	digest := sha256.Sum256([]byte(unsigned))
	sig, _ := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, digest[:])
	token := unsigned + "." + base64.RawURLEncoding.EncodeToString(sig)
	s := &memoryStore{record: Record{ResourceVersion: "1", RepositoryID: "42", OwnerID: "7", Expires: now.Add(time.Minute), Report: Envelope{RunID: "run-1", Target: "target", Repository: "org/repo", Commit: strings.Repeat("a", 40), SchemaSHA256: strings.Repeat("b", 64)}}}
	newHandler := func() *Handler { return &Handler{Policy: p, Keys: staticKeys{"key": &key.PublicKey}, Store: s} }
	call := func(h *Handler, method, path, auth string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, nil)
		if auth != "" {
			r.Header.Set("Authorization", auth)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		return w
	}
	for _, test := range []struct {
		method, path, auth string
		code               int
	}{
		{"GET", "/api-conformance/v1/reports/run-1", "Bearer " + token, 405},
		{"POST", "/api-conformance/v1/reports/run-1?token=secret", "Bearer " + token, 400},
		{"POST", "/api-conformance/v1/reports/run-1", "", 401},
		{"POST", "/api-conformance/v1/reports/run-1", "Bearer invalid", 401},
	} {
		w := call(newHandler(), test.method, test.path, test.auth)
		if w.Code != test.code || s.record.Claimed {
			t.Fatal("unauthenticated access", w.Code)
		}
	}
	h := newHandler()
	w := call(h, "POST", "/api-conformance/v1/reports/run-1", "Bearer "+token)
	if w.Code != 200 || w.Header().Get("Cache-Control") != "no-store" || strings.Contains(w.Body.String(), token) {
		t.Fatal("redemption failed", w.Code)
	}
	if call(h, "POST", "/api-conformance/v1/reports/run-1", "Bearer "+token).Code != 429 {
		t.Fatal("rate limit bypass")
	}
	if call(newHandler(), "POST", "/api-conformance/v1/reports/run-1", "Bearer "+token).Code != 403 {
		t.Fatal("cross-replica replay")
	}
	server := Server(":8080", h)
	if server.ReadHeaderTimeout == 0 || server.WriteTimeout == 0 || server.MaxHeaderBytes == 0 {
		t.Fatal("unbounded server")
	}
}

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
