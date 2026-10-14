package resultproxy

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestIdentityBoundary(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1800000000, 0)
	p := Policy{Audience: "re8ch-report", Repository: "re8ch/cloud-functions", RepositoryID: "1248776882", OwnerID: "283958313", Ref: "refs/heads/master", WorkflowRef: "re8ch/cloud-functions/.github/workflows/re8ch-schemathesis-result.yml@refs/heads/master", WorkflowSHA: strings.Repeat("a", 40)}
	base := Claims{Issuer: Issuer, Audience: p.Audience, Subject: "repo:" + p.Repository + ":ref:" + p.Ref, Repository: p.Repository, RepositoryID: p.RepositoryID, OwnerID: p.OwnerID, Ref: p.Ref, WorkflowRef: p.WorkflowRef, WorkflowSHA: p.WorkflowSHA, Event: "workflow_dispatch", RunID: "123", RunAttempt: "1", JTI: "unique", Expires: now.Unix() + 300, Issued: now.Unix(), NotBefore: now.Unix()}
	sign := func(c Claims, alg string) string {
		h, _ := json.Marshal(map[string]string{"alg": alg, "kid": "test", "typ": "JWT"})
		b, _ := json.Marshal(c)
		unsigned := base64.RawURLEncoding.EncodeToString(h) + "." + base64.RawURLEncoding.EncodeToString(b)
		d := sha256.Sum256([]byte(unsigned))
		s, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, d[:])
		if err != nil {
			t.Fatal(err)
		}
		return unsigned + "." + base64.RawURLEncoding.EncodeToString(s)
	}
	keys := map[string]*rsa.PublicKey{"test": &key.PublicKey}
	if _, err := Verify(sign(base, "RS256"), keys, p, now); err != nil {
		t.Fatal(err)
	}
	immutable := base
	immutable.Subject = "repo:re8ch@283958313/cloud-functions@1248776882:ref:refs/heads/master"
	if _, err := Verify(sign(immutable, "RS256"), keys, p, now); err != nil {
		t.Fatal(err)
	}
	cases := map[string]func(*Claims){
		"repository":    func(c *Claims) { c.Repository = "attacker/cloud-functions" },
		"repository-id": func(c *Claims) { c.RepositoryID = "9" },
		"owner-id":      func(c *Claims) { c.OwnerID = "9" },
		"audience":      func(c *Claims) { c.Audience = "service-credentials" },
		"issuer":        func(c *Claims) { c.Issuer = "https://evil.invalid" },
		"workflow":      func(c *Claims) { c.WorkflowRef = "other" },
		"workflow-sha":  func(c *Claims) { c.WorkflowSHA = strings.Repeat("b", 40) },
		"pull-request":  func(c *Claims) { c.Event = "pull_request" },
		"branch":        func(c *Claims) { c.Ref = "refs/heads/evil" },
		"subject":       func(c *Claims) { c.Subject = "repo:re8ch/cloud-functions:environment:production" },
		"expired":       func(c *Claims) { c.Expires = now.Unix() },
		"future":        func(c *Claims) { c.NotBefore = now.Unix() + 1 },
		"long-lived":    func(c *Claims) { c.Expires = now.Unix() + 3600 },
		"missing-jti":   func(c *Claims) { c.JTI = "" },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			c := base
			change(&c)
			if _, err := Verify(sign(c, "RS256"), keys, p, now); err == nil {
				t.Fatal("accepted")
			}
		})
	}
	for _, token := range []string{sign(base, "HS256"), sign(base, "none"), "invalid", sign(base, "RS256") + "x"} {
		if _, err := Verify(token, keys, p, now); err == nil {
			t.Fatal("accepted invalid signature/algorithm")
		}
	}
	if _, err := Verify(sign(base, "RS256"), nil, p, now); err == nil {
		t.Fatal("accepted unknown key")
	}
}

type memoryStore struct {
	mu     sync.Mutex
	record Record
}

func (s *memoryStore) Get(_ context.Context, _ string) (Record, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.record, nil
}
func (s *memoryStore) Claim(_ context.Context, _, version, _ string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.record.Claimed || s.record.ResourceVersion != version {
		return ErrDenied
	}
	s.record.Claimed = true
	s.record.ResourceVersion = "2"
	return nil
}

func TestOneTimeConcurrentRedemption(t *testing.T) {
	now := time.Now()
	r := Record{ResourceVersion: "1", RepositoryID: "42", OwnerID: "7", Expires: now.Add(time.Minute), Report: Envelope{RunID: "run-1", Target: "target-1", Repository: "org/repo", Commit: strings.Repeat("a", 40), SchemaSHA256: strings.Repeat("b", 64)}}
	c := Claims{Repository: "org/repo", RepositoryID: "42", OwnerID: "7", RunID: "999", RunAttempt: "1", JTI: "unique"}
	for _, change := range []func(*Record){func(r *Record) { r.RepositoryID = "other" }, func(r *Record) { r.OwnerID = "other" }, func(r *Record) { r.Report.Repository = "other/repo" }, func(r *Record) { r.Expires = now }, func(r *Record) { r.Claimed = true }, func(r *Record) { r.Report.Commit = "main" }} {
		bad := r
		change(&bad)
		s := &memoryStore{record: bad}
		if _, err := Redeem(context.Background(), s, "run-1", c, now); err == nil {
			t.Fatal("accepted invalid binding")
		}
	}
	s := &memoryStore{record: r}
	var wg sync.WaitGroup
	results := make(chan bool, 32)
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := Redeem(context.Background(), s, "run-1", c, now)
			results <- err == nil
		}()
	}
	wg.Wait()
	close(results)
	success := 0
	for ok := range results {
		if ok {
			success++
		}
	}
	if success != 1 {
		t.Fatalf("got %d successful redemptions", success)
	}
	if _, err := Redeem(context.Background(), s, "../run-1", c, now); err == nil {
		t.Fatal("accepted invalid path")
	}
}
