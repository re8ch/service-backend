package main

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const defaultStalwartToolEndpoint = "http://mcp-stalwart-agent-tools-shadow.toolhive-system.svc.cluster.local:8088/mcp"

type stalwartConnectionInput struct {
	ConnectionRef string `json:"connectionRef"`
}

type stalwartMailAddress struct {
	Address string `json:"address"`
	Name    string `json:"name,omitempty"`
}

type stalwartMailSendInput struct {
	ConnectionRef  string                `json:"connectionRef"`
	Apply          bool                  `json:"apply,omitempty"`
	From           string                `json:"from,omitempty"`
	To             []stalwartMailAddress `json:"to"`
	CC             []stalwartMailAddress `json:"cc,omitempty"`
	BCC            []stalwartMailAddress `json:"bcc,omitempty"`
	Subject        string                `json:"subject"`
	Text           string                `json:"text"`
	PlanHash       string                `json:"planHash,omitempty"`
	IdempotencyKey string                `json:"idempotencyKey,omitempty"`
}

type stalwartRespondInput struct {
	ConnectionRef  string `json:"connectionRef"`
	EventID        string `json:"eventId"`
	Response       string `json:"response" jsonschema:"accepted, declined or tentative"`
	PlanHash       string `json:"planHash,omitempty"`
	IdempotencyKey string `json:"idempotencyKey,omitempty"`
	Apply          bool   `json:"apply,omitempty"`
}

var stalwartToolEndpoint = func() string {
	if endpoint := os.Getenv("STALWART_TOOL_ENDPOINT"); endpoint != "" {
		return endpoint
	}
	return defaultStalwartToolEndpoint
}

func validatedStalwartToolEndpoint(raw string) (string, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "http" || u.Host != "mcp-stalwart-agent-tools-shadow.toolhive-system.svc.cluster.local:8088" || u.Path != "/mcp" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return "", errors.New("invalid Stalwart tool endpoint")
	}
	return u.String(), nil
}

func stalwartInvocationRequest(id identity, connectionRef string, scopes []string) invocationTokenRequest {
	activeOrg := id.OrganizationRef
	if activeOrg == "" {
		activeOrg = id.Tenant
	}
	toolRef := os.Getenv("STALWART_TOOL_REF")
	if toolRef == "" {
		toolRef = "tool://8iops/stalwart-agent-tools"
	}
	audience := os.Getenv("STALWART_TOOL_AUDIENCE")
	if audience == "" {
		audience = "re8ch-tenant-tool"
	}
	return invocationTokenRequest{
		Subject: id.Subject, ActiveOrgRef: activeOrg, ToolRef: toolRef,
		BindingRef: connectionRef, Scopes: scopes, Audience: audience,
		MaxTTL: invocationTokenMaxTTL, SubjectToken: id.Token,
	}
}

func (s *server) callStalwartTool(ctx context.Context, downstream string, scopes []string, connectionRef string, input any) (any, error) {
	id, err := collaborationCaller(ctx)
	if err != nil {
		return nil, err
	}
	if !invocationRef.MatchString(connectionRef) {
		return nil, errors.New("a valid connectionRef is required")
	}
	issued, err := s.issueInvocationToken(ctx, stalwartInvocationRequest(id, connectionRef, scopes))
	if err != nil {
		return nil, errors.New("Stalwart invocation authorization failed")
	}
	endpoint, err := validatedStalwartToolEndpoint(stalwartToolEndpoint())
	if err != nil {
		return nil, err
	}
	client := mcp.NewClient(&mcp.Implementation{Name: "re8ch-stalwart-relay", Version: "1"}, nil)
	httpClient := &http.Client{
		Timeout:       20 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		Transport:     platformAuthTransport{base: tracedInfrastructureTransport(http.DefaultTransport, endpoint, "collaboration.stalwart.mcp"), token: issued.Token, header: "Authorization"},
	}
	connectCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	session, err := client.Connect(connectCtx, &mcp.StreamableClientTransport{Endpoint: endpoint, HTTPClient: httpClient, DisableStandaloneSSE: true, MaxRetries: -1}, nil)
	if err != nil {
		return nil, errors.New("Stalwart tool endpoint unavailable")
	}
	defer session.Close()
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: downstream, Arguments: input})
	if err != nil {
		return nil, errors.New("Stalwart tool invocation failed")
	}
	if result.IsError {
		return nil, errors.New("Stalwart tool rejected the invocation")
	}
	if result.StructuredContent == nil {
		return nil, errors.New("Stalwart tool returned no structured result")
	}
	return result.StructuredContent, nil
}

func (s *server) registerStalwartRelayTools(ms *mcp.Server) {
	if !collaborationEnabled() {
		return
	}
	mcp.AddTool(ms, &mcp.Tool{Name: "stalwart_capabilities", Description: "Inspect the Stalwart capabilities available to this authorized connection.", Annotations: toolAnnotations("stalwart_capabilities")}, func(ctx context.Context, _ *mcp.CallToolRequest, in stalwartConnectionInput) (*mcp.CallToolResult, any, error) {
		value, err := s.callStalwartTool(ctx, "collaboration_capabilities", []string{"mail.read"}, in.ConnectionRef, in)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "stalwart_mail_query", Description: "List mailboxes or search bounded mail summaries through the Stalwart tool service.", Annotations: toolAnnotations("stalwart_mail_query")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationQueryInput) (*mcp.CallToolResult, any, error) {
		value, err := s.callStalwartTool(ctx, "mail_query", []string{"mail.read"}, in.ConnectionRef, in)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "stalwart_mail_read", Description: "Read a selected authorized message through the Stalwart tool service.", Annotations: toolAnnotations("stalwart_mail_read")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationMailReadInput) (*mcp.CallToolResult, any, error) {
		value, err := s.callStalwartTool(ctx, "mail_read", []string{"mail.read"}, in.ConnectionRef, in)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "stalwart_mail_send", Description: "Plan or explicitly send mail as the bound user through the Stalwart tool service.", Annotations: toolAnnotations("stalwart_mail_send")}, func(ctx context.Context, _ *mcp.CallToolRequest, in stalwartMailSendInput) (*mcp.CallToolResult, any, error) {
		value, err := s.callStalwartTool(ctx, "mail_send", []string{"mail.send"}, in.ConnectionRef, in)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "stalwart_calendar_query", Description: "List calendars, read an event, or query a bounded date window through the Stalwart tool service.", Annotations: toolAnnotations("stalwart_calendar_query")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationQueryInput) (*mcp.CallToolResult, any, error) {
		value, err := s.callStalwartTool(ctx, "calendar_query", []string{"calendar.read"}, in.ConnectionRef, in)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "stalwart_calendar_respond", Description: "Plan or explicitly answer an invitation as the bound user through the Stalwart tool service.", Annotations: toolAnnotations("stalwart_calendar_respond")}, func(ctx context.Context, _ *mcp.CallToolRequest, in stalwartRespondInput) (*mcp.CallToolResult, any, error) {
		value, err := s.callStalwartTool(ctx, "calendar_respond", []string{"calendar.read", "calendar.respond"}, in.ConnectionRef, in)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "stalwart_contacts_query", Description: "List address books or search bounded contacts through the Stalwart tool service.", Annotations: toolAnnotations("stalwart_contacts_query")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationQueryInput) (*mcp.CallToolResult, any, error) {
		value, err := s.callStalwartTool(ctx, "contacts_query", []string{"contacts.read"}, in.ConnectionRef, in)
		return nil, value, err
	})
	for _, pair := range []struct{ published, downstream string }{{"stalwart_files_list", "files_list"}, {"stalwart_files_read", "files_read"}} {
		pair := pair
		mcp.AddTool(ms, &mcp.Tool{Name: pair.published, Description: "Read an authorized WebDAV directory or bounded file through the Stalwart tool service.", Annotations: toolAnnotations(pair.published)}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationFileInput) (*mcp.CallToolResult, any, error) {
			value, err := s.callStalwartTool(ctx, pair.downstream, []string{"files.read"}, in.ConnectionRef, in)
			return nil, value, err
		})
	}
}
