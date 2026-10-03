package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"io"
	"net/http"
	"net/mail"
	"os"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

type userInput struct {
	Action      string `json:"action" jsonschema:"list, get, create, update, delete"`
	UserID      string `json:"userId,omitempty" jsonschema:"Identity UUID for get, update and delete"`
	Email       string `json:"email,omitempty" jsonschema:"Email for create; verification is required before login"`
	DisplayName string `json:"displayName,omitempty"`
	Page        int    `json:"page,omitempty"`
	PerPage     int    `json:"perPage,omitempty"`
}

func userRequest(input userInput) (string, string, map[string]any, error) {
	invalid := errors.New("invalid user action or arguments")
	path := ""
	method := http.MethodGet
	var body map[string]any
	if input.Action != "list" && input.Action != "create" {
		if !organizationUser.MatchString(input.UserID) {
			return "", "", nil, invalid
		}
		path = "/" + input.UserID
	}
	switch input.Action {
	case "list":
		page, size := input.Page, input.PerPage
		if page == 0 {
			page = 1
		}
		if size == 0 {
			size = 50
		}
		if page < 1 || page > 10000 || size < 1 || size > 100 {
			return "", "", nil, invalid
		}
		path = "?page=" + strconv.Itoa(page) + "&perPage=" + strconv.Itoa(size)
	case "get":
	case "create":
		addr, err := mail.ParseAddress(input.Email)
		if err != nil || addr.Address != input.Email || len(input.Email) > 254 {
			return "", "", nil, invalid
		}
		method = http.MethodPost
		body = map[string]any{"email": input.Email}
		if input.DisplayName != "" {
			body["displayName"] = input.DisplayName
		}
	case "update":
		if strings.TrimSpace(input.DisplayName) == "" {
			return "", "", nil, invalid
		}
		method = http.MethodPatch
		body = map[string]any{"displayName": input.DisplayName}
	case "delete":
		method = http.MethodDelete
	default:
		return "", "", nil, invalid
	}
	if utf8.RuneCountInString(input.DisplayName) > 80 {
		return "", "", nil, invalid
	}
	return method, path, body, nil
}
func userCall(ctx context.Context, input userInput) (any, error) {
	id, err := platformIdentity(ctx)
	if err != nil {
		return nil, err
	}
	endpoint := strings.TrimRight(os.Getenv("PLATFORM_USERS_URL"), "/")
	if endpoint == "" {
		return nil, errors.New("user authority is not configured")
	}
	method, path, body, err := userRequest(input)
	if err != nil {
		return nil, err
	}
	payload, _ := json.Marshal(body)
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, method, endpoint+path, bytes.NewReader(payload))
	if err != nil {
		return nil, errors.New("invalid user authority URL")
	}
	req.Header.Set("Authorization", "Bearer "+id.Token)
	req.Header.Set("Content-Type", "application/json")
	// No shared user session and no automatic retries, including write requests.
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(req)
	if err != nil {
		return nil, errors.New("user authority unavailable; outcome may be unknown for writes")
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, (2<<20)+1))
	if err != nil || len(data) > 2<<20 || !json.Valid(data) {
		return nil, errors.New("invalid user authority response")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("user authority rejected request (HTTP %d)", response.StatusCode)
	}
	var result any
	err = json.Unmarshal(data, &result)
	return result, err
}

func (s *server) registerUserTools(ms *mcp.Server) {
	if os.Getenv("PLATFORM_USERS_URL") == "" {
		return
	}
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("platform_user"), Name: "platform_user", Description: "Platform admin identity user CRUD. Actions: list (page/perPage), get (userId), create (email, optional displayName), update (userId, displayName), delete (userId). Create produces an unverified passwordless account; mailbox verification through normal sign-in is required. Delete soft-deletes through GoTrue after organization/resource dependency checks, revoking login; this is not personal-data erasure. Uses the caller's admin login session."}, func(ctx context.Context, _ *mcp.CallToolRequest, input userInput) (*mcp.CallToolResult, any, error) {
		value, err := userCall(ctx, input)
		return nil, value, err
	})
}
