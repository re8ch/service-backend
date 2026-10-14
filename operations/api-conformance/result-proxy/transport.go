package resultproxy

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

type APITransport struct {
	client            *http.Client
	origin, tokenFile string
}

// NewAPITransport accepts deployment configuration, not request parameters.
// The projected service-account token is reread for each call to allow rotation.
func NewAPITransport(origin, caFile, tokenFile string) (*APITransport, error) {
	u, err := url.Parse(origin)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		return nil, ErrDenied
	}
	ca, err := os.ReadFile(caFile)
	if err != nil {
		return nil, ErrDenied
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return nil, ErrDenied
	}
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.Proxy = nil
	t.ResponseHeaderTimeout = 5 * time.Second
	t.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12, RootCAs: pool}
	return &APITransport{origin: origin, tokenFile: tokenFile, client: &http.Client{Transport: t, Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return ErrDenied }}}, nil
}

func (a *APITransport) Request(ctx context.Context, method, path, contentType string, body []byte) ([]byte, error) {
	// Only a named run or its status subresource is reachable; never Secrets.
	p := strings.Split(path, "/")
	if len(p) < 8 || len(p) > 9 || p[0] != "" || p[1] != "apis" || p[2] != "conformance.re8ch.com" || p[3] != "v1alpha1" || p[4] != "namespaces" || !identifier(p[5]) || p[6] != "apiconformanceruns" || !identifier(p[7]) {
		return nil, ErrDenied
	}
	if !(method == "GET" && len(p) == 8 && len(body) == 0 || method == "PATCH" && len(p) == 9 && p[8] == "status" && contentType == "application/json-patch+json" && len(body) <= 4096) {
		return nil, ErrDenied
	}
	token, err := os.ReadFile(a.tokenFile)
	if err != nil || len(token) > 16384 || strings.TrimSpace(string(token)) == "" {
		return nil, ErrDenied
	}
	req, err := http.NewRequestWithContext(ctx, method, a.origin+path, bytes.NewReader(body))
	if err != nil {
		return nil, ErrDenied
	}
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(token)))
	req.Header.Set("Content-Type", contentType)
	resp, err := a.client.Do(req)
	if err != nil {
		return nil, ErrDenied
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, ErrDenied
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 65537))
	if err != nil || len(data) > 65536 {
		return nil, ErrDenied
	}
	return data, nil
}
