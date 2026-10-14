package resultproxy

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

type fakeAPI struct {
	calls                     int
	method, path, contentType string
	body                      []byte
	err                       error
}

func (f *fakeAPI) Request(_ context.Context, method, path, contentType string, body []byte) ([]byte, error) {
	f.calls++
	f.method = method
	f.path = path
	f.contentType = contentType
	f.body = body
	return nil, f.err
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
