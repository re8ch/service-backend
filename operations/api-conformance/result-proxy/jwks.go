package resultproxy

import (
	"context"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"io"
	"math/big"
	"net/http"
	"sync"
	"time"
)

const jwksURL = Issuer + "/.well-known/jwks"

// GitHubKeys never uses URLs from a token. Cache expiry is fail-closed: a
// network failure does not extend old keys. Failed fetches are throttled too.
type GitHubKeys struct {
	mu             sync.Mutex
	keys           map[string]*rsa.PublicKey
	expires, retry time.Time
	client         *http.Client
}

func NewGitHubKeys() *GitHubKeys {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.Proxy = nil
	t.ResponseHeaderTimeout = 5 * time.Second
	return &GitHubKeys{client: &http.Client{Transport: t, Timeout: 5 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return ErrDenied }}}
}

func (g *GitHubKeys) Keys(ctx context.Context) (map[string]*rsa.PublicKey, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	now := time.Now()
	if now.Before(g.expires) {
		return g.keys, nil
	}
	if now.Before(g.retry) || g.client == nil {
		return nil, ErrDenied
	}
	g.retry = now.Add(30 * time.Second)
	req, err := http.NewRequestWithContext(ctx, "GET", jwksURL, nil)
	if err != nil {
		return nil, ErrDenied
	}
	resp, err := g.client.Do(req)
	if err != nil {
		return nil, ErrDenied
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, ErrDenied
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 65537))
	if err != nil || len(body) > 65536 {
		return nil, ErrDenied
	}
	keys, err := parseKeys(body)
	if err != nil {
		return nil, ErrDenied
	}
	g.keys = keys
	g.expires = time.Now().Add(5 * time.Minute)
	return keys, nil
}

func parseKeys(body []byte) (map[string]*rsa.PublicKey, error) {
	var set struct {
		Keys []struct{ Kty, Kid, Use, Alg, N, E string }
	}
	if json.Unmarshal(body, &set) != nil || len(set.Keys) == 0 || len(set.Keys) > 16 {
		return nil, ErrDenied
	}
	keys := make(map[string]*rsa.PublicKey)
	for _, k := range set.Keys {
		if k.Kty != "RSA" || k.Use != "sig" || k.Alg != "RS256" || k.Kid == "" || len(k.Kid) > 256 || keys[k.Kid] != nil {
			return nil, ErrDenied
		}
		n, err := base64.RawURLEncoding.DecodeString(k.N)
		if err != nil || len(n) < 256 || len(n) > 1024 {
			return nil, ErrDenied
		}
		e, err := base64.RawURLEncoding.DecodeString(k.E)
		if err != nil || len(e) == 0 || len(e) > 4 {
			return nil, ErrDenied
		}
		exponent := new(big.Int).SetBytes(e).Int64()
		modulus := new(big.Int).SetBytes(n)
		if exponent < 3 || exponent > 2147483647 || exponent%2 == 0 || modulus.BitLen() < 2048 || modulus.Bit(0) != 1 {
			return nil, ErrDenied
		}
		keys[k.Kid] = &rsa.PublicKey{N: modulus, E: int(exponent)}
	}
	return keys, nil
}
