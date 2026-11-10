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
	"sort"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const modelGatewayCapability = "ai.model-gateway.v1"
const modelGatewayMaxInput = 32768
const modelGatewayMaxResponse = 262144

// This is credential location plus a reviewed safety ceiling, not a key/team
// provisioning system. LiteLLM remains the model and budget authority.
type modelGatewayBinding struct {
	TokenEnv string   `json:"tokenEnv"`
	Models   []string `json:"models"`
}

var modelGatewayTokenEnv = regexp.MustCompile(`^LITELLM_TENANT_[A-Z0-9_]+_KEY$`)

func tenantModelGatewayBinding(tenant string) (modelGatewayBinding, error) {
	var bindings map[string]modelGatewayBinding
	if json.Unmarshal([]byte(os.Getenv("LITELLM_TENANT_BINDINGS_JSON")), &bindings) != nil {
		return modelGatewayBinding{}, errors.New("tenant model gateway binding is not configured")
	}
	binding, ok := bindings[tenant]
	if !ok || !modelGatewayTokenEnv.MatchString(binding.TokenEnv) || len(binding.Models) == 0 || len(binding.Models) > 100 {
		return modelGatewayBinding{}, errors.New("tenant model gateway binding is not configured")
	}
	seen := map[string]bool{}
	for _, model := range binding.Models {
		if strings.TrimSpace(model) != model || model == "" || len(model) > 200 || model == "*" || seen[model] {
			return modelGatewayBinding{}, errors.New("tenant model gateway binding is invalid")
		}
		seen[model] = true
	}
	// Reject accidental reuse of one credential reference across organizations.
	for owner, other := range bindings {
		if owner != tenant && other.TokenEnv == binding.TokenEnv {
			return modelGatewayBinding{}, errors.New("tenant model gateway credential reference must be exclusive")
		}
	}
	return binding, nil
}

func modelGatewayBase() (string, error) {
	base := strings.TrimRight(os.Getenv("LITELLM_GATEWAY_URL"), "/")
	parsed, err := url.Parse(base)
	if err != nil || parsed.Hostname() == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || (parsed.Path != "" && parsed.Path != "/v1") {
		return "", errors.New("model gateway URL is not configured")
	}
	// A fixed internal Kubernetes service may use HTTP. All other destinations
	// require TLS; callers can never choose the endpoint.
	internal := parsed.Scheme == "http" && strings.HasSuffix(parsed.Hostname(), ".svc.cluster.local")
	if parsed.Scheme != "https" && !internal {
		return "", errors.New("model gateway URL requires HTTPS or an internal service")
	}
	if parsed.Path == "" {
		base += "/v1"
	}
	return base, nil
}

func modelGatewayConfigured(tenant string) bool {
	binding, err := tenantModelGatewayBinding(tenant)
	if err != nil || os.Getenv(binding.TokenEnv) == "" {
		return false
	}
	_, err = modelGatewayBase()
	return err == nil
}

func (s *server) registerModelGatewayTools(ms *mcp.Server) {
	definitions, err := legacyDefinitions()
	if err != nil {
		panic(err)
	}
	for _, name := range []string{"model_gateway_catalog", "model_gateway_chat"} {
		definition := definitions[name]
		if definition.Name == "" {
			panic("missing tenant schema: " + name)
		}
		mcp.AddTool(ms, &mcp.Tool{Name: name, Description: definition.Description, InputSchema: definition.InputSchema, Annotations: toolAnnotations(name)},
			func(ctx context.Context, _ *mcp.CallToolRequest, args map[string]any) (*mcp.CallToolResult, any, error) {
				id, err := identityFrom(ctx)
				if err != nil {
					return nil, nil, err
				}
				result, err := s.callModelGateway(ctx, id, name, args)
				return nil, result, err
			})
	}
}

type modelGatewayClient struct {
	base, token string
	http        *http.Client
}

func newModelGatewayClient(tenant string) (*modelGatewayClient, modelGatewayBinding, error) {
	binding, err := tenantModelGatewayBinding(tenant)
	if err != nil {
		return nil, binding, err
	}
	token := os.Getenv(binding.TokenEnv)
	if token == "" {
		return nil, binding, errors.New("tenant model gateway credential is not configured")
	}
	base, err := modelGatewayBase()
	if err != nil {
		return nil, binding, err
	}
	client := &http.Client{Timeout: 45 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect denied") }}
	return &modelGatewayClient{base: base, token: token, http: client}, binding, nil
}

func (c *modelGatewayClient) request(ctx context.Context, method, path string, payload any) (map[string]any, error) {
	var body io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			return nil, errors.New("invalid model gateway payload")
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, body)
	if err != nil {
		return nil, errors.New("invalid model gateway endpoint")
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Content-Type", "application/json")
	response, err := c.http.Do(req)
	if err != nil {
		return nil, errors.New("model gateway request failed; it may have consumed quota; no automatic retry")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("model gateway returned HTTP %d; no automatic retry", response.StatusCode)
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, modelGatewayMaxResponse+1))
	if err != nil || len(raw) > modelGatewayMaxResponse {
		return nil, errors.New("model gateway response unavailable or too large")
	}
	var result map[string]any
	if json.Unmarshal(raw, &result) != nil {
		return nil, errors.New("model gateway returned invalid JSON")
	}
	return result, nil
}

func modelGatewayPayload(args map[string]any, binding modelGatewayBinding) (map[string]any, error) {
	for key := range args {
		if key != "model" && key != "messages" && key != "max_output_tokens" {
			return nil, errors.New("unsupported model gateway argument")
		}
	}
	model := asString(args, "model")
	allowed := false
	for _, candidate := range binding.Models {
		if model == candidate {
			allowed = true
		}
	}
	if !allowed {
		return nil, errors.New("model is outside the reviewed tenant safety ceiling")
	}
	messages, ok := args["messages"].([]any)
	if !ok || len(messages) < 1 || len(messages) > 16 {
		return nil, errors.New("messages must contain 1 to 16 text messages")
	}
	total := 0
	hasUser := false
	for _, raw := range messages {
		message, ok := raw.(map[string]any)
		if !ok || len(message) != 2 {
			return nil, errors.New("messages require only role and content")
		}
		role, rok := message["role"].(string)
		content, cok := message["content"].(string)
		if !rok || !cok || strings.TrimSpace(content) == "" || (role != "system" && role != "user" && role != "assistant") {
			return nil, errors.New("messages require a supported role and nonempty text")
		}
		hasUser = hasUser || role == "user"
		total += len(content)
	}
	if !hasUser || total > modelGatewayMaxInput {
		return nil, errors.New("a user message and at most 32768 content bytes are required")
	}
	maxTokens := float64(1200)
	if raw, exists := args["max_output_tokens"]; exists {
		var ok bool
		maxTokens, ok = raw.(float64)
		if !ok {
			return nil, errors.New("max_output_tokens must be an integer between 1 and 2048")
		}
	}
	if maxTokens < 1 || maxTokens > 2048 || maxTokens != float64(int(maxTokens)) {
		return nil, errors.New("max_output_tokens must be an integer between 1 and 2048")
	}
	return map[string]any{"model": model, "messages": messages, "max_tokens": int(maxTokens), "stream": false, "n": 1}, nil
}

func modelGatewayCompletion(result map[string]any) (map[string]any, error) {
	choices := slice(result["choices"])
	if len(choices) != 1 {
		return nil, errors.New("model gateway returned unexpected choices")
	}
	choice := object(choices[0])
	message := object(choice["message"])
	model, modelOK := result["model"].(string)
	content, contentOK := message["content"].(string)
	finish := asString(choice, "finish_reason")
	if !modelOK || model == "" || !contentOK || finish != "stop" || message["tool_calls"] != nil || message["function_call"] != nil {
		return nil, errors.New("model gateway completion incomplete or not plain text; no automatic retry")
	}
	usage := map[string]any{}
	for _, key := range []string{"prompt_tokens", "completion_tokens", "total_tokens"} {
		if value, ok := object(result["usage"])[key].(float64); ok && value >= 0 && value == float64(int64(value)) {
			usage[key] = value
		}
	}
	return map[string]any{"model": model, "content": content, "finishReason": finish, "usage": usage, "synthetic": true, "browserExecuted": false}, nil
}

func (s *server) callModelGateway(ctx context.Context, id identity, name string, args map[string]any) (any, error) {
	if id.Service || id.Subject == "" || id.Tenant == "" {
		return nil, errors.New("an authenticated tenant member is required")
	}
	if err := requireGrantedConsumable(ctx, id, modelGatewayCapability); err != nil {
		return nil, err
	}
	client, binding, err := newModelGatewayClient(id.Tenant)
	if err != nil {
		return nil, err
	}
	switch name {
	case "model_gateway_catalog":
		if len(args) != 0 {
			return nil, errors.New("model catalog takes no arguments")
		}
		result, err := client.request(ctx, http.MethodGet, "/models", nil)
		if err != nil {
			return nil, err
		}
		data, ok := result["data"].([]any)
		if !ok {
			return nil, errors.New("model gateway returned an invalid catalog")
		}
		native := map[string]bool{}
		for _, item := range data {
			native[asString(object(item), "id")] = true
		}
		models := []string{}
		for _, model := range binding.Models {
			if native[model] {
				models = append(models, model)
			}
		}
		sort.Strings(models)
		return map[string]any{"tenantRef": id.Tenant, "models": models, "budgetAuthority": "LiteLLM virtual key and team", "maxInputBytes": modelGatewayMaxInput, "maxOutputTokens": 2048, "streaming": false}, nil
	case "model_gateway_chat":
		payload, err := modelGatewayPayload(args, binding)
		if err != nil {
			return nil, err
		}
		result, err := client.request(ctx, http.MethodPost, "/chat/completions", payload)
		if err != nil {
			return nil, err
		}
		return modelGatewayCompletion(result)
	default:
		return nil, errors.New("unknown model gateway operation")
	}
}
