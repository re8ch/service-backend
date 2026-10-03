package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var organizationRef = regexp.MustCompile(`^[a-z0-9](?:[-a-z0-9]{0,78}[a-z0-9])?$`)
var organizationUser = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

type organizationInput struct {
	Action      string `json:"action" jsonschema:"list, get, members, create, rename, archive, add_member, set_member_role, or remove_member"`
	TenantRef   string `json:"tenantRef,omitempty"`
	DisplayName string `json:"displayName,omitempty"`
	ParentRef   string `json:"parentRef,omitempty"`
	UserID      string `json:"userId,omitempty" jsonschema:"Existing identity UUID; adding a member does not send an invitation"`
	Role        string `json:"role,omitempty"`
	Limit       int    `json:"limit,omitempty"`
	Offset      int    `json:"offset,omitempty"`
}

func organizationRequest(input organizationInput) (string, string, map[string]any, error) {
	invalid := errors.New("invalid organization action or arguments")
	path, method := "", http.MethodGet
	body := map[string]any{}
	if input.Action != "list" && input.Action != "create" {
		if !organizationRef.MatchString(input.TenantRef) {
			return "", "", nil, invalid
		}
		path = "/" + input.TenantRef
	}
	switch input.Action {
	case "list", "get", "members":
		if input.Action == "members" {
			path += "/members"
		}
		limit := input.Limit
		if limit == 0 {
			limit = 100
		}
		if limit < 1 || limit > 250 || input.Offset < 0 {
			return "", "", nil, invalid
		}
		query := url.Values{"limit": {strconv.Itoa(limit)}, "offset": {strconv.Itoa(input.Offset)}}
		path += "?" + query.Encode()
	case "create":
		if !organizationRef.MatchString(input.TenantRef) || strings.TrimSpace(input.DisplayName) == "" || (input.ParentRef != "" && !organizationRef.MatchString(input.ParentRef)) {
			return "", "", nil, invalid
		}
		method = http.MethodPost
		body = map[string]any{"tenantRef": input.TenantRef, "displayName": input.DisplayName, "parentRef": input.ParentRef}
	case "rename":
		if strings.TrimSpace(input.DisplayName) == "" {
			return "", "", nil, invalid
		}
		method = http.MethodPatch
		body = map[string]any{"displayName": input.DisplayName}
	case "archive":
		method = http.MethodDelete
	case "add_member", "set_member_role", "remove_member":
		if !organizationUser.MatchString(input.UserID) {
			return "", "", nil, invalid
		}
		path += "/members"
		method = http.MethodPost
		body = map[string]any{"userId": input.UserID, "role": input.Role}
		if input.Action != "add_member" {
			path += "/" + input.UserID
			method = http.MethodPatch
			body = map[string]any{"role": input.Role}
		}
		if input.Action == "remove_member" {
			method = http.MethodDelete
			body = nil
		}
	default:
		return "", "", nil, invalid
	}
	return method, path, body, nil
}

func organizationCall(ctx context.Context, input organizationInput) (any, error) {
	id, err := platformIdentity(ctx)
	if err != nil {
		return nil, err
	}
	endpoint := strings.TrimRight(os.Getenv("PLATFORM_ORGANIZATIONS_URL"), "/")
	if endpoint == "" {
		return nil, errors.New("organization authority is not configured")
	}
	method, path, body, err := organizationRequest(input)
	if err != nil {
		return nil, err
	}
	payload, _ := json.Marshal(body)
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, method, endpoint+path, bytes.NewReader(payload))
	if err != nil {
		return nil, errors.New("invalid organization authority URL")
	}
	req.Header.Set("Authorization", "Bearer "+id.Token)
	req.Header.Set("Content-Type", "application/json")
	// No shared user session and no automatic retries, including write requests.
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return nil, errors.New("organization authority unavailable; outcome may be unknown for writes")
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, (2<<20)+1))
	if err != nil || len(data) > 2<<20 || !json.Valid(data) {
		return nil, errors.New("invalid organization authority response")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("organization authority rejected request (HTTP %d)", response.StatusCode)
	}
	var result any
	err = json.Unmarshal(data, &result)
	return result, err
}

func (s *server) registerOrganizationTools(ms *mcp.Server) {
	if os.Getenv("PLATFORM_ORGANIZATIONS_URL") == "" {
		return
	}
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_organization"), Name: "platform_organization", Description: "Manage identity organizations and existing members through the identity authority. Admin login session required. Actions: list, get, members, create, rename, archive, add_member, set_member_role, remove_member. Archive requires dependency checks. Resource grants and quotas belong to tenant-control."}, func(ctx context.Context, _ *mcp.CallToolRequest, input organizationInput) (*mcp.CallToolResult, any, error) {
		value, err := organizationCall(ctx, input)
		return nil, value, err
	})
}
