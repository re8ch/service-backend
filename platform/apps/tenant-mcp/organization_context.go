package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type organizationContextInput struct {
	TenantRef string `json:"tenantRef" jsonschema:"Organization returned by tenant_organization_context_list"`
}

type organizationContext struct {
	TenantRef string `json:"tenant_ref"`
	Role      string `json:"tenant_role"`
	Kind      string `json:"tenant_kind"`
	Name      string `json:"display_name"`
	ParentRef string `json:"parent_ref"`
	Active    bool   `json:"is_active"`
}

func identityRPC(ctx context.Context, id identity, endpoint string, body any) ([]byte, error) {
	if id.Service || id.Subject == "" || id.Token == "" {
		return nil, errors.New("an authenticated user is required")
	}
	payload, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return nil, errors.New("invalid identity authority URL")
	}
	req.Header.Set("Authorization", "Bearer "+id.Token)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	resp, err := (&http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}).Do(req)
	if err != nil {
		return nil, errors.New("identity authority unavailable")
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, (1<<20)+1))
	if err != nil || len(data) > 1<<20 {
		return nil, errors.New("invalid identity authority response")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("identity authority rejected request (HTTP %d)", resp.StatusCode)
	}
	if !json.Valid(data) {
		return nil, errors.New("invalid identity authority response")
	}
	return data, nil
}

func contextRPCURL(name string) string {
	base := strings.TrimRight(os.Getenv("IDENTITY_REST_URL"), "/")
	if base == "" {
		base = "http://supabase-identity-rest-ro.supabase-identity.svc:3000"
	}
	return base + "/rpc/" + name
}

func (s *server) registerOrganizationContextTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_organization_context_list"), Name: "tenant_organization_context_list", Description: "List organizations available to this account and identify the single active resource context."},
		func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			id, ok := ctx.Value(identityKey{}).(identity)
			if !ok {
				return nil, nil, errors.New("an authenticated user is required")
			}
			data, err := identityRPC(ctx, id, contextRPCURL("my_tenant_runtime_contexts"), map[string]any{})
			if err != nil {
				return nil, nil, err
			}
			var rows []organizationContext
			if err := json.Unmarshal(data, &rows); err != nil {
				return nil, nil, errors.New("invalid organization context response")
			}
			items := make([]map[string]any, 0, len(rows))
			for _, row := range rows {
				items = append(items, map[string]any{"tenantRef": row.TenantRef, "role": row.Role, "kind": row.Kind, "displayName": row.Name, "parentRef": row.ParentRef, "active": row.Active})
			}
			return nil, map[string]any{"organizations": items}, nil
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("tenant_organization_context_select"), Name: "tenant_organization_context_select", Description: "Select the account-wide active organization. The next MCP request uses the new resource context."},
		func(ctx context.Context, _ *mcp.CallToolRequest, input organizationContextInput) (*mcp.CallToolResult, any, error) {
			id, ok := ctx.Value(identityKey{}).(identity)
			if !ok || !organizationRef.MatchString(input.TenantRef) {
				return nil, nil, errors.New("a valid organization is required")
			}
			data, err := identityRPC(ctx, id, contextRPCURL("select_active_organization"), map[string]string{"p_tenant_ref": input.TenantRef})
			if err != nil {
				return nil, nil, err
			}
			var result map[string]any
			if err := json.Unmarshal(data, &result); err != nil {
				return nil, nil, errors.New("invalid organization context response")
			}
			result["effectiveOn"] = "next_request"
			return nil, result, nil
		})
}
