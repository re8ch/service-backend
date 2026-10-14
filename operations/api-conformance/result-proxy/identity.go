// Package resultproxy implements the fail-closed report redemption boundary.
// It deliberately has no service credential or business API access.
package resultproxy

import (
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

var ErrDenied = errors.New("report access denied")

const Issuer = "https://token.actions.githubusercontent.com"

// Policy is reviewed deployment configuration, never supplied by the caller.
type Policy struct {
	Audience, Repository, RepositoryID, OwnerID, Ref, WorkflowRef, WorkflowSHA string
}

type Claims struct {
	Issuer       string `json:"iss"`
	Audience     string `json:"aud"`
	Subject      string `json:"sub"`
	Repository   string `json:"repository"`
	RepositoryID string `json:"repository_id"`
	OwnerID      string `json:"repository_owner_id"`
	Ref          string `json:"ref"`
	WorkflowRef  string `json:"workflow_ref"`
	WorkflowSHA  string `json:"workflow_sha"`
	Event        string `json:"event_name"`
	RunID        string `json:"run_id"`
	RunAttempt   string `json:"run_attempt"`
	JTI          string `json:"jti"`
	Expires      int64  `json:"exp"`
	Issued       int64  `json:"iat"`
	NotBefore    int64  `json:"nbf"`
}

func decimal(s string) bool {
	if s == "" {
		return false
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

func commit(s string) bool {
	if len(s) != 40 {
		return false
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

// Verify accepts only RS256 keys obtained independently from the fixed issuer.
// Token-provided jku/x5u URLs are never followed. All failures are opaque.
func Verify(token string, keys map[string]*rsa.PublicKey, p Policy, now time.Time) (Claims, error) {
	var c Claims
	parts := strings.Split(token, ".")
	if len(token) > 16384 || len(parts) != 3 {
		return c, ErrDenied
	}
	var header struct {
		Algorithm string   `json:"alg"`
		KeyID     string   `json:"kid"`
		Type      string   `json:"typ"`
		Critical  []string `json:"crit"`
	}
	h, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil || json.Unmarshal(h, &header) != nil || header.Algorithm != "RS256" || header.Type != "JWT" || len(header.Critical) != 0 {
		return c, ErrDenied
	}
	key := keys[header.KeyID]
	if key == nil || key.N.BitLen() < 2048 {
		return c, ErrDenied
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return c, ErrDenied
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], sig) != nil {
		return c, ErrDenied
	}
	body, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil || json.Unmarshal(body, &c) != nil {
		return Claims{}, ErrDenied
	}
	owners := strings.Split(p.Repository, "/")
	if len(owners) != 2 || owners[0] == "" || owners[1] == "" || p.Audience == "" || !decimal(p.RepositoryID) || !decimal(p.OwnerID) || !commit(p.WorkflowSHA) || !strings.HasPrefix(p.Ref, "refs/heads/") || !strings.HasPrefix(p.WorkflowRef, p.Repository+"/.github/workflows/") || !strings.HasSuffix(p.WorkflowRef, "@"+p.Ref) {
		return Claims{}, ErrDenied
	}
	legacy := "repo:" + p.Repository + ":ref:" + p.Ref
	immutable := "repo:" + owners[0] + "@" + p.OwnerID + "/" + owners[1] + "@" + p.RepositoryID + ":ref:" + p.Ref
	t := now.Unix()
	if c.Issuer != Issuer || c.Audience != p.Audience || (c.Subject != legacy && c.Subject != immutable) || c.Repository != p.Repository || c.RepositoryID != p.RepositoryID || c.OwnerID != p.OwnerID || c.Ref != p.Ref || c.WorkflowRef != p.WorkflowRef || c.WorkflowSHA != p.WorkflowSHA || c.Event != "workflow_dispatch" || !decimal(c.RunID) || !decimal(c.RunAttempt) || c.JTI == "" || c.Expires <= t || c.Issued <= 0 || c.Issued > t || c.NotBefore <= 0 || c.NotBefore > t || c.Expires <= c.Issued || c.Expires-c.Issued > 600 || t-c.Issued > 600 {
		return Claims{}, ErrDenied
	}
	return c, nil
}
