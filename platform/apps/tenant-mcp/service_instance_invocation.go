package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type serviceInstanceCapabilitiesInput struct {
	ResourceRef string `json:"resourceRef" jsonschema:"Service instance reference returned by service_instance_list"`
	BindingRef  string `json:"bindingRef" jsonschema:"User-owned Ready binding or connection reference"`
}

type serviceInstanceInvokeInput struct {
	ResourceRef string         `json:"resourceRef" jsonschema:"Service instance reference returned by service_instance_list"`
	BindingRef  string         `json:"bindingRef" jsonschema:"User-owned Ready binding or connection reference"`
	Operation   string         `json:"operation" jsonschema:"Exact operation returned by service_instance_capabilities"`
	Arguments   map[string]any `json:"arguments,omitempty" jsonschema:"Arguments validated by the selected provider operation schema"`
}

func (s *server) serviceInstanceProvider(ctx context.Context, id identity, ref string) (string, map[string]any, error) {
	name, err := resourceName(id.Tenant, ref)
	if err != nil {
		return "", nil, errors.New("a tenant-owned service instance is required")
	}
	instance, err := controlRequest(ctx, id.Tenant, "/v1/services/"+url.PathEscape(name))
	if err != nil {
		return "", nil, err
	}
	spec, status := object(instance["spec"]), object(instance["status"])
	serviceClass, phase := asString(spec, "serviceClass"), asString(status, "phase")
	if serviceClass == "collaboration.stalwart.v1" && phase != "Ready" && phase != "AuthorizationRequired" {
		return "", nil, errors.New("Stalwart service instance is not available")
	}
	return serviceClass, instance, nil
}

func (s *server) readyCollaborationScopes(ctx context.Context, id identity, bindingRef string) ([]string, error) {
	if !invocationRef.MatchString(bindingRef) {
		return nil, errors.New("a valid bindingRef is required")
	}
	connection, err := s.collaborationConnection(ctx, id, bindingRef)
	if err != nil {
		return nil, err
	}
	if connection.public()["state"] != "Ready" {
		return nil, errors.New("collaboration binding is not Ready")
	}
	var scopes []string
	if json.Unmarshal([]byte(connection.Scopes), &scopes) != nil || len(scopes) == 0 {
		return nil, errors.New("collaboration binding scopes are invalid")
	}
	return scopes, nil
}

func (s *server) connectStalwartTools(ctx context.Context, id identity, bindingRef string, scopes []string) (*mcp.ClientSession, context.CancelFunc, error) {
	issued, err := s.issueInvocationToken(ctx, stalwartInvocationRequest(id, bindingRef, scopes))
	if err != nil {
		return nil, nil, errors.New("Stalwart invocation authorization failed")
	}
	endpoint, err := validatedStalwartToolEndpoint(stalwartToolEndpoint())
	if err != nil {
		return nil, nil, err
	}
	client := mcp.NewClient(&mcp.Implementation{Name: "re8ch-service-instance", Version: "1"}, nil)
	httpClient := &http.Client{
		Timeout:       20 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		Transport:     platformAuthTransport{base: tracedInfrastructureTransport(http.DefaultTransport, endpoint, "service.instance.mcp"), token: issued.Token, header: "Authorization"},
	}
	connectCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	session, err := client.Connect(connectCtx, &mcp.StreamableClientTransport{Endpoint: endpoint, HTTPClient: httpClient, DisableStandaloneSSE: true, MaxRetries: -1}, nil)
	if err != nil {
		cancel()
		return nil, nil, errors.New("Stalwart tool endpoint unavailable")
	}
	return session, cancel, nil
}

func (s *server) serviceInstanceCapabilities(ctx context.Context, id identity, input serviceInstanceCapabilitiesInput) (any, error) {
	serviceClass, _, err := s.serviceInstanceProvider(ctx, id, input.ResourceRef)
	if err != nil {
		return nil, err
	}
	if serviceClass != "collaboration.stalwart.v1" {
		return nil, errors.New("service instance does not publish an invocation provider")
	}
	scopes, err := s.readyCollaborationScopes(ctx, id, input.BindingRef)
	if err != nil {
		return nil, err
	}
	session, cancel, err := s.connectStalwartTools(ctx, id, input.BindingRef, scopes)
	if err != nil {
		return nil, err
	}
	defer cancel()
	defer session.Close()
	listed, err := session.ListTools(ctx, nil)
	if err != nil {
		return nil, errors.New("Stalwart capability discovery failed")
	}
	return map[string]any{
		"resourceRef": input.ResourceRef, "bindingRef": input.BindingRef,
		"serviceClass": serviceClass, "operations": listed.Tools,
		"bindingScopes": scopes, "credentialMaterialReturned": false,
	}, nil
}

func (s *server) serviceInstanceInvoke(ctx context.Context, id identity, input serviceInstanceInvokeInput) (any, error) {
	serviceClass, _, err := s.serviceInstanceProvider(ctx, id, input.ResourceRef)
	if err != nil {
		return nil, err
	}
	if serviceClass != "collaboration.stalwart.v1" {
		return nil, errors.New("service instance does not publish an invocation provider")
	}
	if !mcpToolName.MatchString(input.Operation) {
		return nil, errors.New("a valid provider operation is required")
	}
	scopes, err := s.readyCollaborationScopes(ctx, id, input.BindingRef)
	if err != nil {
		return nil, err
	}
	arguments, err := boundProviderArguments(input.BindingRef, input.Arguments)
	if err != nil {
		return nil, err
	}
	session, cancel, err := s.connectStalwartTools(ctx, id, input.BindingRef, scopes)
	if err != nil {
		return nil, err
	}
	defer cancel()
	defer session.Close()
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: input.Operation, Arguments: arguments})
	if err != nil {
		return nil, errors.New("service instance operation failed")
	}
	if result.IsError {
		return nil, errors.New("service instance provider rejected the operation")
	}
	if result.StructuredContent == nil {
		return nil, errors.New("service instance provider returned no structured result")
	}
	return result.StructuredContent, nil
}

func boundProviderArguments(bindingRef string, input map[string]any) (map[string]any, error) {
	arguments := make(map[string]any, len(input)+1)
	for key, value := range input {
		arguments[key] = value
	}
	if supplied := asString(arguments, "connectionRef"); supplied != "" && supplied != bindingRef {
		return nil, errors.New("provider arguments cannot override bindingRef")
	}
	arguments["connectionRef"] = bindingRef
	return arguments, nil
}

func (s *server) registerServiceInstanceInvocationTools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Name: "service_instance_capabilities", Description: "Discover the live operation schemas exposed by a tenant-owned service instance through a Ready user binding.", Annotations: toolAnnotations("service_instance_capabilities")}, func(ctx context.Context, _ *mcp.CallToolRequest, input serviceInstanceCapabilitiesInput) (*mcp.CallToolResult, any, error) {
		id, err := ownerScopedIdentityFrom(ctx)
		if err != nil {
			return nil, nil, err
		}
		value, err := s.serviceInstanceCapabilities(ctx, id, input)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "service_instance_invoke", Description: "Invoke an exact operation returned by service_instance_capabilities. The server binds the active organization, service instance, Ready user binding and its granted scopes before provider dispatch.", Annotations: toolAnnotations("service_instance_invoke")}, func(ctx context.Context, _ *mcp.CallToolRequest, input serviceInstanceInvokeInput) (*mcp.CallToolResult, any, error) {
		id, err := ownerScopedIdentityFrom(ctx)
		if err != nil {
			return nil, nil, err
		}
		value, err := s.serviceInstanceInvoke(ctx, id, input)
		return nil, value, err
	})
}
