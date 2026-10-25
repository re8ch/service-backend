package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
)

const maxOAuthTokenBody = 1 << 20

// oauthTokenCompat preserves GoTrue's token endpoint while normalizing its
// historical error body for strict RFC 6749 clients. Successful responses are
// passed through byte-for-byte; legacy error fields are retained alongside the
// standard fields so older clients remain compatible.
func oauthTokenCompat(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxOAuthTokenBody))
	if err != nil {
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{"error": "invalid_request", "error_description": "token request is too large"})
		return
	}
	upstream := os.Getenv("OAUTH_TOKEN_UPSTREAM_URL")
	if upstream == "" {
		upstream = "http://supabase-identity-auth.supabase-identity.svc:9999/oauth/token"
	}
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, upstream, bytes.NewReader(body))
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "temporarily_unavailable", "error_description": "authorization server unavailable"})
		return
	}
	for _, name := range []string{"Content-Type", "Authorization", "Accept", "User-Agent"} {
		if value := r.Header.Get(name); value != "" {
			req.Header.Set(name, value)
		}
	}
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "temporarily_unavailable", "error_description": "authorization server unavailable"})
		return
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, maxOAuthTokenBody+1))
	if err != nil || len(raw) > maxOAuthTokenBody {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "server_error", "error_description": "invalid authorization server response"})
		return
	}
	if response.StatusCode >= 400 {
		raw = normalizeOAuthError(raw, response.StatusCode)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Pragma", "no-cache")
	w.WriteHeader(response.StatusCode)
	_, _ = w.Write(raw)
}

func normalizeOAuthError(raw []byte, status int) []byte {
	var value map[string]any
	if json.Unmarshal(raw, &value) != nil {
		value = map[string]any{}
	}
	if _, ok := value["error"]; !ok {
		code, _ := value["error_code"].(string)
		// GoTrue error_code values are useful diagnostics but are not OAuth
		// error codes. Map authentication failures to the standard value that
		// strict clients use to discard stale credentials and reauthorize.
		switch code {
		case "invalid_credentials", "refresh_token_not_found", "refresh_token_already_used":
			code = "invalid_grant"
		case "bad_json", "validation_failed", "request_timeout":
			code = "invalid_request"
		case "unexpected_failure":
			code = "server_error"
		case "":
			code = "server_error"
			if status == http.StatusBadRequest || status == http.StatusUnauthorized {
				code = "invalid_grant"
			}
		default:
			// Unknown vendor codes stay available in error_code but must not
			// make strict clients fail to deserialize the OAuth response.
			code = "invalid_grant"
		}
		value["error"] = strings.ToLower(code)
	}
	if _, ok := value["error_description"]; !ok {
		description, _ := value["msg"].(string)
		if description == "" {
			description = "authorization server rejected the token request"
		}
		value["error_description"] = description
	}
	normalized, _ := json.Marshal(value)
	return append(normalized, '\n')
}
