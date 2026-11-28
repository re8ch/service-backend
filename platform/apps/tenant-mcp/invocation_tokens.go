package main

import (
	"context"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

const invocationTokenMaxTTL = 300

var invocationRef = regexp.MustCompile(`^[a-z][a-z0-9+.-]*://[^[:space:]]{1,512}$`)
var invocationScope = regexp.MustCompile(`^[a-z][a-z0-9:._/-]{0,127}$`)

type invocationTokenRequest struct {
	Subject      string   `json:"subject"`
	ActiveOrgRef string   `json:"activeOrgRef"`
	ToolRef      string   `json:"toolRef"`
	BindingRef   string   `json:"bindingRef"`
	Scopes       []string `json:"scopes"`
	Audience     string   `json:"audience"`
	MaxTTL       int      `json:"maxTtlSeconds"`
	SubjectToken string   `json:"subjectToken"`
}
type issuedInvocationToken struct {
	Token     string   `json:"accessToken"`
	ExpiresIn int      `json:"expiresIn"`
	Scopes    []string `json:"scopes"`
	claims    *invocationClaims
}
type invocationTokenIssuer interface {
	Issue(context.Context, invocationTokenRequest) (issuedInvocationToken, error)
}
type httpInvocationTokenIssuer struct{ endpoint, serviceToken, expectedIssuer, jwksURL string }
type invocationClaims struct {
	Issuer, Subject, JTI, ActiveOrgRef, OwnerOrgRef, ToolRef, BindingRef string
	Audience                                                             any
	Scopes                                                               []string
	Expires                                                              int64
}
type invocationJWTClaims struct {
	ActiveOrgRef string   `json:"active_org_ref"`
	OwnerOrgRef  string   `json:"owner_org_ref"`
	ToolRef      string   `json:"tool_ref"`
	BindingRef   string   `json:"binding_ref"`
	Scopes       []string `json:"scopes"`
	jwt.RegisteredClaims
}

func invocationTokenRequestFromForm(form url.Values, id identity, audience string) (invocationTokenRequest, error) {
	toolRef, bindingRef := form.Get("tool_ref"), form.Get("binding_ref")
	if !invocationRef.MatchString(toolRef) || !invocationRef.MatchString(bindingRef) {
		return invocationTokenRequest{}, errors.New("tool_ref and binding_ref are required opaque references")
	}
	scopes := strings.Fields(form.Get("scope"))
	if len(scopes) < 1 || len(scopes) > 16 {
		return invocationTokenRequest{}, errors.New("scope must contain 1 to 16 values")
	}
	seen := map[string]bool{}
	for _, scope := range scopes {
		if !invocationScope.MatchString(scope) || seen[scope] {
			return invocationTokenRequest{}, errors.New("scope contains an invalid or duplicate value")
		}
		seen[scope] = true
	}
	return invocationTokenRequest{Subject: id.Subject, ActiveOrgRef: id.OrganizationRef, ToolRef: toolRef, BindingRef: bindingRef, Scopes: scopes, Audience: audience, MaxTTL: invocationTokenMaxTTL, SubjectToken: id.Token}, nil
}

func (s *server) issueInvocationToken(ctx context.Context, request invocationTokenRequest) (issuedInvocationToken, error) {
	issuer := s.invocationIssuer
	if issuer == nil {
		endpoint, secret, expected, jwksURL := os.Getenv("INVOCATION_TOKEN_ISSUER_URL"), os.Getenv("TENANT_CLAIM_SERVICE_TOKEN"), os.Getenv("INVOCATION_TOKEN_ISSUER"), os.Getenv("INVOCATION_TOKEN_JWKS_URL")
		if endpoint == "" || secret == "" || expected == "" || jwksURL == "" {
			return issuedInvocationToken{}, errors.New("invocation token issuer is not configured")
		}
		if validateAuthorityURL(endpoint) != nil || validateAuthorityURL(jwksURL) != nil {
			return issuedInvocationToken{}, errors.New("invalid invocation token authority URL")
		}
		issuer = &httpInvocationTokenIssuer{endpoint: endpoint, serviceToken: secret, expectedIssuer: expected, jwksURL: jwksURL}
	}
	issued, err := issuer.Issue(ctx, request)
	if err != nil {
		return issuedInvocationToken{}, err
	}
	if issued.claims == nil {
		return issuedInvocationToken{}, errors.New("issuer did not provide cryptographically verified claims")
	}
	claims := *issued.claims
	now := time.Now().Unix()
	if claims.Subject != request.Subject || claims.ActiveOrgRef != request.ActiveOrgRef || claims.ToolRef != request.ToolRef || claims.BindingRef != request.BindingRef || !tenantName.MatchString(claims.OwnerOrgRef) || claims.JTI == "" || !hasAudience(claims.Audience) || claims.Expires <= now || claims.Expires > now+invocationTokenMaxTTL || !sameScopes(claims.Scopes, request.Scopes) || !sameScopes(claims.Scopes, issued.Scopes) {
		return issuedInvocationToken{}, errors.New("issuer returned an invalid downscoped token")
	}
	if issued.ExpiresIn < 1 || issued.ExpiresIn > invocationTokenMaxTTL {
		return issuedInvocationToken{}, errors.New("issuer returned an invalid lifetime")
	}
	return issued, nil
}

func (h *httpInvocationTokenIssuer) Issue(ctx context.Context, request invocationTokenRequest) (issuedInvocationToken, error) {
	body, _ := json.Marshal(request)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.endpoint, strings.NewReader(string(body)))
	if err != nil {
		return issuedInvocationToken{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Tenant-Service-Token", h.serviceToken)
	req.Header.Set("X-Tenant-Caller", "tenant")
	resp, err := (&http.Client{Timeout: 5 * time.Second, Transport: tracedInfrastructureTransport(http.DefaultTransport, h.endpoint, "identity.invocation_token.http")}).Do(req)
	if err != nil {
		return issuedInvocationToken{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return issuedInvocationToken{}, fmt.Errorf("issuer returned HTTP %d", resp.StatusCode)
	}
	var issued issuedInvocationToken
	if json.NewDecoder(io.LimitReader(resp.Body, 65536)).Decode(&issued) != nil {
		return issuedInvocationToken{}, errors.New("invalid issuer response")
	}
	claims, err := verifyInvocationJWT(ctx, issued.Token, h.expectedIssuer, h.jwksURL)
	if err != nil {
		return issuedInvocationToken{}, err
	}
	issued.claims = &claims
	return issued, nil
}

func validateAuthorityURL(raw string) error {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Host == "" || (parsed.Scheme != "https" && !(parsed.Scheme == "http" && (strings.HasSuffix(parsed.Hostname(), ".svc") || parsed.Hostname() == "127.0.0.1"))) {
		return errors.New("authority URL must be HTTPS or an in-cluster service")
	}
	return nil
}

func verifyInvocationJWT(ctx context.Context, raw, expectedIssuer, jwksURL string) (invocationClaims, error) {
	if expectedIssuer == "" || validateAuthorityURL(jwksURL) != nil {
		return invocationClaims{}, errors.New("invocation verifier is not configured")
	}
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, jwksURL, nil)
	resp, err := (&http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}).Do(req)
	if err != nil {
		return invocationClaims{}, fmt.Errorf("JWKS unavailable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return invocationClaims{}, fmt.Errorf("JWKS returned HTTP %d", resp.StatusCode)
	}
	var set struct {
		Keys []struct {
			Kty string `json:"kty"`
			Kid string `json:"kid"`
			Alg string `json:"alg"`
			N   string `json:"n"`
			E   string `json:"e"`
		} `json:"keys"`
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 65536)).Decode(&set) != nil {
		return invocationClaims{}, errors.New("invalid JWKS")
	}
	keys := map[string]*rsa.PublicKey{}
	for _, key := range set.Keys {
		if key.Kty != "RSA" || key.Alg != "RS256" || key.Kid == "" {
			continue
		}
		n, ne := base64.RawURLEncoding.DecodeString(key.N)
		e, ee := base64.RawURLEncoding.DecodeString(key.E)
		if ne != nil || ee != nil || len(e) == 0 || len(e) > 4 {
			continue
		}
		exponent := 0
		for _, value := range e {
			exponent = exponent<<8 + int(value)
		}
		if exponent >= 3 {
			keys[key.Kid] = &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: exponent}
		}
	}
	claims := &invocationJWTClaims{}
	token, err := jwt.ParseWithClaims(raw, claims, func(token *jwt.Token) (any, error) {
		if token.Method.Alg() != jwt.SigningMethodRS256.Alg() {
			return nil, errors.New("unsupported JWT algorithm")
		}
		kid, _ := token.Header["kid"].(string)
		key := keys[kid]
		if key == nil {
			return nil, errors.New("unknown JWT kid")
		}
		return key, nil
	}, jwt.WithValidMethods([]string{"RS256"}), jwt.WithIssuer(expectedIssuer), jwt.WithExpirationRequired())
	if err != nil || !token.Valid || claims.ExpiresAt == nil {
		return invocationClaims{}, errors.New("invalid invocation token signature or claims")
	}
	audience := make([]any, len(claims.Audience))
	for i, value := range claims.Audience {
		audience[i] = value
	}
	return invocationClaims{Issuer: claims.Issuer, Audience: audience, Subject: claims.Subject, Expires: claims.ExpiresAt.Unix(), JTI: claims.ID, ActiveOrgRef: claims.ActiveOrgRef, OwnerOrgRef: claims.OwnerOrgRef, ToolRef: claims.ToolRef, BindingRef: claims.BindingRef, Scopes: claims.Scopes}, nil
}

func audienceContains(raw any, expected string) bool {
	if value, ok := raw.(string); ok {
		return value == expected
	}
	values, ok := raw.([]any)
	if !ok {
		return false
	}
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}
func hasAudience(raw any) bool {
	if value, ok := raw.(string); ok {
		return value != ""
	}
	values, ok := raw.([]any)
	return ok && len(values) == 1 && values[0] != ""
}
func sameScopes(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	seen := map[string]bool{}
	for _, value := range left {
		seen[value] = true
	}
	for _, value := range right {
		if !seen[value] {
			return false
		}
	}
	return true
}
