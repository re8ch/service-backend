package main

import (
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

func TestRedemptionIsSingleUseAndTenantBound(t *testing.T) {
	s := testServer(t)
	var err error
	s.redemptions, err = openRedemptions(filepath.Join(t.TempDir(), "devices.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.redemptions.Close() })
	result, err := s.issueRedemption("credential://harbor/acme/0123456789abcdef0123456789abcdef", "acme")
	if err != nil {
		t.Fatal(err)
	}
	token := strings.Split(result["redemption_url"].(string), "#")[1]
	ref, tenant, err := s.consumeRedemption(token, "credential_redemptions")
	if err != nil || ref != "credential://harbor/acme/0123456789abcdef0123456789abcdef" || tenant != "acme" {
		t.Fatalf("consume: %s %s %v", ref, tenant, err)
	}
	if _, _, err = s.consumeRedemption(token, "credential_redemptions"); err == nil {
		t.Fatal("token redeemed twice")
	}
	if err = readyHumanCredential(nil, "other", ref); err == nil {
		t.Fatal("cross-tenant credential accepted")
	}
}

func TestCredentialPageUsesFragmentToken(t *testing.T) {
	s := testServer(t)
	request := httptest.NewRequest("GET", "/credentials/redeem", nil)
	response := httptest.NewRecorder()
	s.handler().ServeHTTP(response, request)
	if response.Code != 200 || !strings.Contains(response.Body.String(), "location.hash.slice(1)") || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("credential page: %d", response.Code)
	}
}
