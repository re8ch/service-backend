package resultproxy

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"time"
)

// Envelope contains only typed summary fields, not arbitrary runner JSON.
// Textual errors, request/response bodies and credentials have no representation.
type Envelope struct {
	RunID        string `json:"runId"`
	Target       string `json:"target"`
	Repository   string `json:"repository"`
	Commit       string `json:"commit"`
	SchemaSHA256 string `json:"schemaSha256"`
	Passed       bool   `json:"passed"`
	Requests     uint32 `json:"requests"`
	Operations   uint32 `json:"operations"`
}

type Record struct {
	ResourceVersion string
	RepositoryID    string
	OwnerID         string
	Expires         time.Time
	Claimed         bool
	Report          Envelope
}

// Store implementations must use a Kubernetes resourceVersion test and a
// single atomic status patch. A read followed by an unconditional write is not
// sufficient. A CAS conflict MUST fail, never retry against a fresh version.
type Store interface {
	Get(context.Context, string) (Record, error)
	Claim(context.Context, string, string, string) error
}

func identifier(s string) bool {
	if len(s) == 0 || len(s) > 253 {
		return false
	}
	for i, c := range s {
		if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '-' && i > 0 && i < len(s)-1) {
			return false
		}
	}
	return true
}

// Redeem is called only after Verify. Consume before returning bytes: a lost
// response is an uncertain delivery, not permission to replay the same claim.
func Redeem(ctx context.Context, store Store, runID string, c Claims, now time.Time) ([]byte, error) {
	if !identifier(runID) {
		return nil, ErrDenied
	}
	r, err := store.Get(ctx, runID)
	if err != nil || r.Claimed || r.ResourceVersion == "" || !r.Expires.After(now) || r.RepositoryID != c.RepositoryID || r.OwnerID != c.OwnerID || r.Report.Repository != c.Repository || r.Report.RunID != runID || !identifier(r.Report.Target) || !commit(r.Report.Commit) {
		return nil, ErrDenied
	}
	schema, err := hex.DecodeString(r.Report.SchemaSHA256)
	if err != nil || len(schema) != 32 {
		return nil, ErrDenied
	}
	body, err := json.Marshal(r.Report)
	if err != nil {
		return nil, ErrDenied
	}
	// Persist an audit fingerprint, not the OIDC token or its raw claims.
	fingerprint := sha256.Sum256([]byte(c.RepositoryID + ":" + c.RunID + ":" + c.RunAttempt + ":" + c.JTI))
	if store.Claim(ctx, runID, r.ResourceVersion, hex.EncodeToString(fingerprint[:])) != nil {
		return nil, ErrDenied
	}
	return body, nil
}
