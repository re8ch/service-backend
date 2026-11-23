package main

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

type fakeInvocationIssuer struct {
	key             *rsa.PrivateKey
	jwksURL, issuer string
}
type invocationIssuerFunc func(context.Context, invocationTokenRequest) (issuedInvocationToken, error)

func (f invocationIssuerFunc) Issue(ctx context.Context, request invocationTokenRequest) (issuedInvocationToken, error) {
	return f(ctx, request)
}

func testInvocationIssuer(t *testing.T) *fakeInvocationIssuer {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	e := big.NewInt(int64(key.PublicKey.E)).Bytes()
	jwks, _ := json.Marshal(map[string]any{"keys": []any{map[string]any{"kty": "RSA", "kid": "current", "alg": "RS256", "use": "sig", "n": base64.RawURLEncoding.EncodeToString(key.PublicKey.N.Bytes()), "e": base64.RawURLEncoding.EncodeToString(e)}}})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(jwks)
	}))
	t.Cleanup(server.Close)
	return &fakeInvocationIssuer{key: key, jwksURL: server.URL, issuer: "https://issuer.re8ch.test"}
}

func invocationRegisteredClaims(request invocationTokenRequest, issuer string) invocationJWTClaims {
	return invocationJWTClaims{ActiveOrgRef: request.ActiveOrgRef, OwnerOrgRef: "publisher", ToolRef: request.ToolRef, BindingRef: request.BindingRef, Scopes: request.Scopes, RegisteredClaims: jwt.RegisteredClaims{Issuer: issuer, Subject: request.Subject, Audience: jwt.ClaimStrings{request.Audience}, ExpiresAt: jwt.NewNumericDate(time.Now().Add(2 * time.Minute)), ID: "invocation-1"}}
}

func (f *fakeInvocationIssuer) Issue(ctx context.Context, request invocationTokenRequest) (issuedInvocationToken, error) {
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, invocationRegisteredClaims(request, f.issuer))
	token.Header["kid"] = "current"
	raw, err := token.SignedString(f.key)
	if err != nil {
		return issuedInvocationToken{}, err
	}
	claims, err := verifyInvocationJWT(ctx, raw, f.issuer, f.jwksURL)
	if err != nil {
		return issuedInvocationToken{}, err
	}
	return issuedInvocationToken{Token: raw, ExpiresIn: 120, Scopes: request.Scopes, claims: &claims}, nil
}

func TestInvocationRequestUsesVerifiedActiveOrganization(t *testing.T) {
	form := url.Values{"tool_ref": {"tool://publisher/search"}, "binding_ref": {"binding://acme/one"}, "scope": {"tools:invoke usage:write"}, "active_org_ref": {"attacker"}, "owner_org_ref": {"attacker"}}
	request, err := invocationTokenRequestFromForm(form, identity{Subject: "user-1", OrganizationRef: "acme"}, "re8ch-tool")
	if err != nil {
		t.Fatal(err)
	}
	if request.ActiveOrgRef != "acme" {
		t.Fatalf("request overrode active org: %#v", request)
	}
}

func TestInvocationIssuerFailsClosedAndValidatesBoundClaims(t *testing.T) {
	t.Setenv("INVOCATION_TOKEN_ISSUER_URL", "")
	request := invocationTokenRequest{Subject: "user-1", ActiveOrgRef: "acme", ToolRef: "tool://publisher/search", BindingRef: "binding://acme/one", Scopes: []string{"tools:invoke"}, Audience: "re8ch-tool", MaxTTL: invocationTokenMaxTTL}
	if _, err := (&server{}).issueInvocationToken(context.Background(), request); err == nil {
		t.Fatal("missing issuer configuration must fail closed")
	}
	authority := testInvocationIssuer(t)
	server := &server{invocationIssuer: authority}
	if _, err := server.issueInvocationToken(context.Background(), request); err != nil {
		t.Fatalf("valid signed token rejected: %v", err)
	}
	server.invocationIssuer = invocationIssuerFunc(func(ctx context.Context, escalated invocationTokenRequest) (issuedInvocationToken, error) {
		escalated.Scopes = append(escalated.Scopes, "admin:all")
		return authority.Issue(ctx, escalated)
	})
	if _, err := server.issueInvocationToken(context.Background(), request); err == nil {
		t.Fatal("issuer scope escalation must be rejected")
	}
}

func TestInvocationJWTRejectsUntrustedTokens(t *testing.T) {
	authority := testInvocationIssuer(t)
	request := invocationTokenRequest{Subject: "user-1", ActiveOrgRef: "acme", ToolRef: "tool://publisher/search", BindingRef: "binding://acme/one", Scopes: []string{"tools:invoke"}, Audience: "re8ch-tool"}
	valid, _ := authority.Issue(context.Background(), request)
	otherKey, _ := rsa.GenerateKey(rand.Reader, 2048)
	sign := func(method jwt.SigningMethod, kid, issuer string, key any) string {
		token := jwt.NewWithClaims(method, invocationRegisteredClaims(request, issuer))
		token.Header["kid"] = kid
		raw, _ := token.SignedString(key)
		return raw
	}
	payload, _ := json.Marshal(invocationRegisteredClaims(request, authority.issuer))
	none := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"none","kid":"current"}`)) + "." + base64.RawURLEncoding.EncodeToString(payload) + "."
	cases := map[string]string{"alg none": none, "unknown alg": sign(jwt.SigningMethodHS256, "current", authority.issuer, []byte("not-a-jwks-key")), "unknown kid": sign(jwt.SigningMethodRS256, "retired", authority.issuer, authority.key), "bad signature": sign(jwt.SigningMethodRS256, "current", authority.issuer, otherKey), "issuer mismatch": sign(jwt.SigningMethodRS256, "current", "https://attacker.example", authority.key)}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := verifyInvocationJWT(context.Background(), raw, authority.issuer, authority.jwksURL); err == nil {
				t.Fatal("untrusted token accepted")
			}
		})
	}
	if _, err := verifyInvocationJWT(context.Background(), valid.Token, authority.issuer, "http://127.0.0.1:1/jwks"); err == nil {
		t.Fatal("unavailable JWKS must fail closed")
	}
}
