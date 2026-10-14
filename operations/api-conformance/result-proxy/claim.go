package resultproxy

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"regexp"
	"time"
)

// Envelope contains only typed summary fields, not arbitrary runner JSON.
// Textual errors, request/response bodies and credentials have no representation.
type Envelope struct {
	RunID        string   `json:"runId"`
	Target       string   `json:"target"`
	Repository   string   `json:"repository"`
	Commit       string   `json:"commit"`
	ImageDigest  string   `json:"imageDigest,omitempty"`
	SchemaSHA256 string   `json:"schemaSha256"`
	Passed       bool     `json:"passed"`
	Requests     uint32   `json:"requests,omitempty"`
	Operations   uint32   `json:"operations"`
	Summary      *Summary `json:"summary,omitempty"`
}

type Summary struct {
	Version       int               `json:"version"`
	Target        string            `json:"target"`
	RunID         string            `json:"runId"`
	Mode          string            `json:"mode"`
	Engine        string            `json:"engine"`
	EngineVersion string            `json:"engineVersion"`
	Seed          uint64            `json:"seed"`
	SchemaSHA256  string            `json:"schemaSha256"`
	Status        string            `json:"status"`
	ExitCode      int               `json:"exitCode"`
	Counts        map[string]uint32 `json:"counts"`
	Fingerprints  []string          `json:"failureFingerprints"`
	Lifecycle     map[string]string `json:"lifecycle"`
	AuditRefs     []string          `json:"auditRefs"`
}

func validSummary(e Envelope) bool {
	s := e.Summary
	if s == nil {
		return true
	} // Initial envelope format; controller emits full summaries.
	if s.Version != 1 || s.Target != e.Target || s.RunID != e.RunID || s.SchemaSHA256 != e.SchemaSHA256 || (s.Mode != "read" && s.Mode != "mutation") || s.Engine != "schemathesis" || s.EngineVersion != "4.29.1" || (s.Status != "passed" && s.Status != "failed") || (s.Status == "passed") != e.Passed || s.ExitCode < -255 || s.ExitCode > 255 {
		return false
	}
	keys := []string{"generated", "withFailures", "withoutChecks", "uniqueFailures", "operationsTotal", "operationsSelected", "operationsTested", "operationsErrored", "operationsSkipped"}
	if len(s.Counts) != len(keys) || len(s.Lifecycle) != 4 || len(s.Fingerprints) > 50 || len(s.AuditRefs) > 20 {
		return false
	}
	for _, k := range keys {
		v, ok := s.Counts[k]
		if !ok || v > 1000000 {
			return false
		}
	}
	for _, k := range []string{"setup", "test", "cleanup", "reversal"} {
		v := s.Lifecycle[k]
		if v != "not_run" && v != "passed" && v != "failed" && v != "skipped" {
			return false
		}
	}
	for _, v := range s.Fingerprints {
		if !regexp.MustCompile(`^[a-f0-9]{20}$`).MatchString(v) {
			return false
		}
	}
	for _, v := range s.AuditRefs {
		if !regexp.MustCompile(`^audit://[A-Za-z0-9_./-]{1,200}$`).MatchString(v) {
			return false
		}
	}
	if e.ImageDigest != "" && !regexp.MustCompile(`^sha256:[a-f0-9]{64}$`).MatchString(e.ImageDigest) {
		return false
	}
	if s.Counts["operationsTested"] != e.Operations {
		return false
	}
	if e.Passed {
		if s.ExitCode != 0 || s.Counts["operationsSelected"] == 0 || s.Counts["operationsSelected"] != s.Counts["operationsTested"] || s.Counts["generated"] <= s.Counts["withoutChecks"] || len(s.Fingerprints) > 0 {
			return false
		}
		for _, k := range []string{"withFailures", "uniqueFailures", "operationsErrored", "operationsSkipped"} {
			if s.Counts[k] != 0 {
				return false
			}
		}
		for _, v := range s.Lifecycle {
			if v == "failed" {
				return false
			}
		}
	}
	return true
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
	if err != nil || r.Claimed || r.ResourceVersion == "" || !r.Expires.After(now) || r.RepositoryID != c.RepositoryID || r.OwnerID != c.OwnerID || r.Report.Repository != c.Repository || r.Report.RunID != runID || !identifier(r.Report.Target) || !commit(r.Report.Commit) || !validSummary(r.Report) {
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
