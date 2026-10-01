package main

import (
	"context"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

const supabaseIssuer = "https://db-rw.re8ch.com/auth/v1"

type identity struct {
	Subject          string
	Token            string
	Tenant           string
	Role             string
	DefaultNamespace string
	Service          bool
}

type identityKey struct{}

var errInvalidToken = errors.New("invalid bearer token")

func identityFrom(ctx context.Context) (identity, error) {
	value, ok := ctx.Value(identityKey{}).(identity)
	if !ok || value.Service || value.Subject == "" || value.Tenant == "" {
		return identity{}, errors.New("an authenticated tenant member is required")
	}
	return value, nil
}

type jwtClaims struct {
	Issuer   string `json:"iss"`
	Audience string `json:"aud"`
	Subject  string `json:"sub"`
	Expires  int64  `json:"exp"`
}

func decodeClaims(token string) (jwtClaims, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return jwtClaims{}, errors.New("invalid JWT")
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return jwtClaims{}, err
	}
	var claims jwtClaims
	if err = json.Unmarshal(raw, &claims); err != nil {
		return jwtClaims{}, err
	}
	if claims.Issuer != supabaseIssuer || claims.Audience != "authenticated" || claims.Subject == "" || claims.Expires <= time.Now().Unix() {
		return jwtClaims{}, errors.New("invalid JWT claims")
	}
	return claims, nil
}

func serviceToken(token string) bool {
	secret := os.Getenv("SERVICE_API_TOKEN")
	return secret != "" && subtle.ConstantTimeCompare([]byte(token), []byte(secret)) == 1
}

func (s *server) authenticate(ctx context.Context, bearer, selected string) (identity, error) {
	if serviceToken(bearer) {
		return identity{Subject: "service:litellm", Service: true}, nil
	}
	if selected == "admin" || (selected != "" && !tenantName.MatchString(selected)) {
		return identity{}, errors.New("invalid bearer token or tenant selection")
	}
	id, err := s.verifySupabase(ctx, bearer)
	if err != nil {
		return identity{}, fmt.Errorf("%w: %v", errInvalidToken, err)
	}
	controlURL := strings.TrimRight(os.Getenv("TENANT_CONTROL_URL"), "/")
	secret := os.Getenv("TENANT_CLAIM_SERVICE_TOKEN")
	if controlURL == "" || secret == "" {
		return identity{}, errors.New("tenant authorizer is unavailable")
	}
	payload, _ := json.Marshal(map[string]string{"tenantRef": selected, "accessToken": bearer})
	resolve, err := http.NewRequestWithContext(ctx, http.MethodPost, controlURL+"/v1/resolve-supabase", strings.NewReader(string(payload)))
	if err != nil {
		return identity{}, err
	}
	resolve.Header.Set("Content-Type", "application/json")
	resolve.Header.Set("X-Tenant-Caller", "tenant")
	resolve.Header.Set("X-Tenant-Service-Token", secret)
	client := &http.Client{Timeout: 8 * time.Second}
	response2, err := client.Do(resolve)
	if err != nil {
		return identity{}, fmt.Errorf("tenant authorizer unavailable: %w", err)
	}
	defer response2.Body.Close()
	if response2.StatusCode != http.StatusOK {
		return identity{}, errors.New("identity is not an active member of the selected tenant")
	}
	var membership struct {
		Tenant           string `json:"tenantRef"`
		Role             string `json:"role"`
		DefaultNamespace string `json:"defaultNamespace"`
	}
	if err = json.NewDecoder(io.LimitReader(response2.Body, 65536)).Decode(&membership); err != nil {
		return identity{}, err
	}
	if !tenantName.MatchString(membership.Tenant) || membership.Tenant == "admin" || (selected != "" && selected != membership.Tenant) || membership.DefaultNamespace == "" || (membership.Role != "viewer" && membership.Role != "operator" && membership.Role != "admin") {
		return identity{}, errors.New("tenant authorizer returned an invalid context")
	}
	id.Tenant, id.Role, id.DefaultNamespace = membership.Tenant, membership.Role, membership.DefaultNamespace
	return id, nil
}

func (s *server) verifySupabase(ctx context.Context, bearer string) (identity, error) {
	if bearer == "" || len(bearer) > 16384 {
		return identity{}, errors.New("invalid bearer token")
	}
	claims, err := decodeClaims(bearer)
	if err != nil {
		return identity{}, err
	}
	userURL := os.Getenv("SUPABASE_USER_URL")
	if userURL == "" {
		userURL = "http://supabase-identity-auth.supabase-identity.svc:9999/user"
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, userURL, nil)
	if err != nil {
		return identity{}, err
	}
	req.Header.Set("Authorization", "Bearer "+bearer)
	req.Header.Set("Accept", "application/json")
	client := &http.Client{Timeout: 5 * time.Second}
	response, err := client.Do(req)
	if err != nil {
		return identity{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return identity{}, errors.New("GoTrue rejected bearer token")
	}
	var user struct {
		ID          string `json:"id"`
		IsAnonymous bool   `json:"is_anonymous"`
	}
	if err = json.NewDecoder(io.LimitReader(response.Body, 65536)).Decode(&user); err != nil || user.ID != claims.Subject || user.IsAnonymous {
		return identity{}, errors.New("GoTrue subject mismatch")
	}
	return identity{Subject: user.ID, Token: bearer}, nil
}
