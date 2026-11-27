package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

const collaborationBrokerMaxBody = 16 << 10

type collaborationBrokerResolveRequest struct {
	ConnectionRef  string   `json:"connectionRef"`
	RequiredScopes []string `json:"requiredScopes"`
}

func (s *server) collaborationBrokerResolveHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	authenticated, authErr := collaborationBrokerWorkloadAuthenticated(r)
	if authErr != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "workload_identity_unavailable"})
		return
	}
	if !authenticated {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid_workload_identity"})
		return
	}
	invocation := strings.TrimPrefix(r.Header.Get("X-RE8CH-Invocation"), "Bearer ")
	issuer, jwks := os.Getenv("INVOCATION_TOKEN_ISSUER"), os.Getenv("INVOCATION_TOKEN_JWKS_URL")
	claims, err := verifyInvocationJWT(r.Context(), invocation, issuer, jwks)
	if err != nil {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid_invocation"})
		return
	}
	var input collaborationBrokerResolveRequest
	decoder := json.NewDecoder(io.LimitReader(r.Body, collaborationBrokerMaxBody+1))
	decoder.DisallowUnknownFields()
	if r.ContentLength > collaborationBrokerMaxBody || decoder.Decode(&input) != nil || !invocationRef.MatchString(input.ConnectionRef) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid_request"})
		return
	}
	selected, _, err := collaborationScopes(input.RequiredScopes)
	if err != nil || len(selected) != len(input.RequiredScopes) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid_scopes"})
		return
	}
	audience := os.Getenv("STALWART_TOOL_AUDIENCE")
	if audience == "" {
		audience = "re8ch-tenant-tool"
	}
	toolRef, ownerRef := os.Getenv("STALWART_TOOL_REF"), os.Getenv("STALWART_TOOL_OWNER_ORG_REF")
	now := time.Now().Unix()
	if !audienceContains(claims.Audience, audience) || claims.BindingRef != input.ConnectionRef || claims.ToolRef != toolRef || toolRef == "" || claims.OwnerOrgRef != ownerRef || ownerRef == "" || claims.JTI == "" || claims.Subject == "" || !tenantName.MatchString(claims.ActiveOrgRef) || claims.Expires <= now || claims.Expires > now+invocationTokenMaxTTL || !scopeSubset(selected, claims.Scopes) {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "invocation_boundary_mismatch"})
		return
	}
	id := identity{Tenant: claims.ActiveOrgRef, Subject: claims.Subject, OrganizationRef: claims.ActiveOrgRef}
	access, origin, err := s.resolveCollaborationAccess(r.Context(), id, input.ConnectionRef)
	if err != nil {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "binding_unavailable"})
		return
	}
	if !scopeSubset(selected, access.Scopes) {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "scope_not_granted"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"connectionRef": input.ConnectionRef,
		"origin":        origin,
		"bearerToken":   access.Bearer,
		"accountId":     access.AccountID,
		"scopes":        access.Scopes,
		"fileRoots":     access.FileRoots,
		"expiresAt":     access.ExpiresAt.UTC(),
	})
}

var collaborationBrokerWorkloadAuthenticated = verifyCollaborationBrokerWorkload

func verifyCollaborationBrokerWorkload(r *http.Request) (bool, error) {
	username := os.Getenv("STALWART_TOOL_CLIENT")
	header := r.Header.Get("Authorization")
	if username == "" || !strings.HasPrefix(header, "Bearer ") || len(header) > 16391 {
		return false, nil
	}
	client, base, serviceToken, err := kubeClient()
	if err != nil {
		return false, err
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	payload, _ := json.Marshal(map[string]any{"apiVersion": "authentication.k8s.io/v1", "kind": "TokenReview", "spec": map[string]any{"token": strings.TrimPrefix(header, "Bearer "), "audiences": []string{"stalwart-binding-broker"}}})
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, base+"/apis/authentication.k8s.io/v1/tokenreviews", strings.NewReader(string(payload)))
	if err != nil {
		return false, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+serviceToken)
	response, err := client.Do(request)
	if err != nil {
		return false, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK && response.StatusCode != http.StatusCreated {
		return false, errors.New("Kubernetes TokenReview failed")
	}
	var result struct {
		Status struct {
			Authenticated bool `json:"authenticated"`
			User          struct {
				Username string `json:"username"`
			} `json:"user"`
			Audiences []string `json:"audiences"`
		} `json:"status"`
	}
	if json.NewDecoder(io.LimitReader(response.Body, 65536)).Decode(&result) != nil {
		return false, errors.New("invalid Kubernetes TokenReview")
	}
	for _, audience := range result.Status.Audiences {
		if audience == "stalwart-binding-broker" && result.Status.Authenticated && result.Status.User.Username == username {
			return true, nil
		}
	}
	return false, nil
}

func scopeSubset(required, available []string) bool {
	granted := map[string]bool{}
	for _, scope := range available {
		granted[scope] = true
	}
	for _, scope := range required {
		if !granted[scope] {
			return false
		}
	}
	return true
}
