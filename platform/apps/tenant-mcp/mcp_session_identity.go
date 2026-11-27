package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/modelcontextprotocol/go-sdk/auth"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// withVerifiedMCPSession supplies the SDK's session principal after authenticate
// has already verified the bearer and resolved the current organization. It does
// not replace verification, cache a grant, or bind a session to raw token bytes.
func withVerifiedMCPSession(next http.Handler) http.Handler {
	return auth.RequireBearerToken(func(ctx context.Context, _ string, _ *http.Request) (*auth.TokenInfo, error) {
		id, ok := ctx.Value(identityKey{}).(identity)
		if !ok || id.Subject == "" {
			return nil, auth.ErrInvalidToken
		}
		principal, _ := json.Marshal(struct {
			Subject, Organization, Tenant, Runtime string
			Service                                bool
		}{
			id.Subject, id.OrganizationRef, id.Tenant, id.RuntimeTenantRef, id.Service,
		})
		return &auth.TokenInfo{UserID: fmt.Sprintf("%x", sha256.Sum256(principal)),
			Extra: map[string]any{"re8ch.verified-identity": id, "re8ch.tool-diagnostics": diagnosticState(ctx)}}, nil
	}, &auth.RequireBearerTokenOptions{
		// Expiration/revocation have just been checked by authenticate on this very
		// request. Only its verified principal is being adapted into SDK context.
		AllowMissingExpiration: true,
	})(next)
}

// Stateful SDK sessions retain their initialization context. Use the verified
// identity attached to this HTTP request so refreshed credentials and current
// authorization decisions, rather than initialization-time values, reach tools.
func verifiedMCPSessionIdentity(next mcp.MethodHandler) mcp.MethodHandler {
	return func(ctx context.Context, method string, request mcp.Request) (mcp.Result, error) {
		if extra := request.GetExtra(); extra != nil && extra.TokenInfo != nil {
			if id, ok := extra.TokenInfo.Extra["re8ch.verified-identity"].(identity); ok {
				ctx = context.WithValue(ctx, identityKey{}, id)
			}
		}
		return next(ctx, method, request)
	}
}
