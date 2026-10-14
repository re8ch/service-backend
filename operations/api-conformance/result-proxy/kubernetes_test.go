package resultproxy

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

type fakeAPI struct {
	calls                     int
	method, path, contentType string
	body                      []byte
	response                  []byte
	err                       error
}

func (f *fakeAPI) Request(_ context.Context, method, path, contentType string, body []byte) ([]byte, error) {
	f.calls++
	f.method = method
	f.path = path
	f.contentType = contentType
	f.body = body
	return f.response, f.err
}

func TestKubernetesCompletedRecord(t *testing.T) {
	f := &fakeAPI{response: []byte(`{"metadata":{"resourceVersion":"123"},"status":{"phase":"Succeeded","delivery":{"repositoryId":"42","ownerId":"7","expiresAt":"2026-10-14T10:00:00Z","claimed":false,"report":{"runId":"run-1","target":"target-1","repository":"org/repo"}}}}`)}
	s := KubernetesStore{API: f, Namespace: "api-conformance-system"}
	r, err := s.Get(context.Background(), "run-1")
	if err != nil || r.ResourceVersion != "123" || r.RepositoryID != "42" || r.OwnerID != "7" || r.Claimed || r.Report.RunID != "run-1" || r.Expires.IsZero() {
		t.Fatal("invalid record projection")
	}
	for _, body := range []string{`{}`, `invalid`, `{"status":{"phase":"Running"}}`, strings.Repeat("x", 65537)} {
		f.response = []byte(body)
		if _, err := s.Get(context.Background(), "run-1"); err == nil {
			t.Fatal("accepted invalid/incomplete record")
		}
	}
	f.err = errors.New("unavailable")
	if _, err := s.Get(context.Background(), "run-1"); err == nil {
		t.Fatal("accepted API failure")
	}
}
func TestKubernetesCAS(t *testing.T) {
	f := &fakeAPI{}
	s := KubernetesStore{API: f, Namespace: "api-conformance-system"}
	if err := s.Claim(context.Background(), "run-1", "123", "hash"); err != nil {
		t.Fatal(err)
	}
	var patch []map[string]any
	if json.Unmarshal(f.body, &patch) != nil || len(patch) != 4 {
		t.Fatal("invalid patch")
	}
	if patch[0]["op"] != "test" || patch[0]["path"] != "/metadata/resourceVersion" || patch[0]["value"] != "123" || patch[1]["op"] != "test" || patch[1]["value"] != false || patch[2]["value"] != true {
		t.Fatal("missing atomic preconditions")
	}
	if f.method != "PATCH" || f.path != "/apis/conformance.re8ch.com/v1alpha1/namespaces/api-conformance-system/apiconformanceruns/run-1/status" || f.contentType != "application/json-patch+json" {
		t.Fatal("incorrect status request")
	}
	f.err = errors.New("conflict")
	before := f.calls
	if s.Claim(context.Background(), "run-1", "123", "hash") == nil || f.calls != before+1 {
		t.Fatal("conflict must fail without retry")
	}
	before = f.calls
	if s.Claim(context.Background(), "../secrets", "123", "hash") == nil || f.calls != before {
		t.Fatal("invalid path reached transport")
	}
}
