package resultproxy

import (
	"context"
	"encoding/json"
	"time"
)

// KubernetesTransport must authenticate to the configured API server, validate
// its TLS certificate, reject redirects and bound response size and timeout.
// Errors must not include server response bodies or authorization headers.
type KubernetesTransport interface {
	Request(context.Context, string, string, string, []byte) ([]byte, error)
}

type KubernetesStore struct {
	API       KubernetesTransport
	Namespace string
}

func (s KubernetesStore) path(name string) (string, error) {
	if !identifier(name) || !identifier(s.Namespace) || s.API == nil {
		return "", ErrDenied
	}
	return "/apis/conformance.re8ch.com/v1alpha1/namespaces/" + s.Namespace + "/apiconformanceruns/" + name, nil
}

func (s KubernetesStore) Get(ctx context.Context, name string) (Record, error) {
	path, err := s.path(name)
	if err != nil {
		return Record{}, ErrDenied
	}
	body, err := s.API.Request(ctx, "GET", path, "application/json", nil)
	if err != nil || len(body) > 65536 {
		return Record{}, ErrDenied
	}
	var run struct {
		Metadata struct {
			ResourceVersion string `json:"resourceVersion"`
		} `json:"metadata"`
		Status struct {
			Phase    string `json:"phase"`
			Delivery struct {
				RepositoryID string    `json:"repositoryId"`
				OwnerID      string    `json:"ownerId"`
				Expires      time.Time `json:"expiresAt"`
				Claimed      bool      `json:"claimed"`
				Report       Envelope  `json:"report"`
			} `json:"delivery"`
		} `json:"status"`
	}
	if json.Unmarshal(body, &run) != nil || (run.Status.Phase != "Succeeded" && run.Status.Phase != "Failed") {
		return Record{}, ErrDenied
	}
	d := run.Status.Delivery
	return Record{ResourceVersion: run.Metadata.ResourceVersion, RepositoryID: d.RepositoryID, OwnerID: d.OwnerID, Expires: d.Expires, Claimed: d.Claimed, Report: d.Report}, nil
}

func (s KubernetesStore) Claim(ctx context.Context, name, version, fingerprint string) error {
	path, err := s.path(name)
	if err != nil || version == "" {
		return ErrDenied
	}
	patch, _ := json.Marshal([]map[string]any{
		{"op": "test", "path": "/metadata/resourceVersion", "value": version},
		{"op": "test", "path": "/status/delivery/claimed", "value": false},
		{"op": "replace", "path": "/status/delivery/claimed", "value": true},
		{"op": "add", "path": "/status/delivery/claimFingerprint", "value": fingerprint},
	})
	_, err = s.API.Request(ctx, "PATCH", path+"/status", "application/json-patch+json", patch)
	if err != nil {
		return ErrDenied
	}
	return nil
}
