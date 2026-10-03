package main

import (
	"net/http/httptest"
	"testing"
)

func TestPublicDomainChallenge(t *testing.T) {
	for _, token := range []string{"", "bad\nvalue", "public-test-challenge_12345"} {
		t.Run(token, func(t *testing.T) {
			t.Setenv("OPENAI_APPS_CHALLENGE_TOKEN", token)
			req := httptest.NewRequest("GET", "/.well-known/openai-apps-challenge", nil)
			response := httptest.NewRecorder()
			(&server{}).handler().ServeHTTP(response, req)
			if token == "public-test-challenge_12345" {
				if response.Code != 200 || response.Body.String() != token || response.Header().Get("Cache-Control") != "no-store" {
					t.Fatalf("unexpected public challenge response: %d %q", response.Code, response.Body.String())
				}
			} else if response.Code != 404 {
				t.Fatalf("unconfigured/invalid challenge must return 404: %d", response.Code)
			}
		})
	}
}
