package main

import (
	"net/http"
	"os"
	"regexp"
)

var publicChallengePattern = regexp.MustCompile(`^[A-Za-z0-9_-]{16,256}$`)

// The domain verification value is intentionally public, not an API credential.
func openAIChallenge(w http.ResponseWriter, r *http.Request) {
	token := os.Getenv("OPENAI_APPS_CHALLENGE_TOKEN")
	if !publicChallengePattern.MatchString(token) {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(token))
}
