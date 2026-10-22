package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"net/url"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

//go:embed tenant_tools.json
var tenantToolSchemas []byte

type toolDefinition struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	InputSchema json.RawMessage `json:"inputSchema"`
}

func legacyDefinitions() (map[string]toolDefinition, error) {
	var items []toolDefinition
	if err := json.Unmarshal(tenantToolSchemas, &items); err != nil {
		return nil, err
	}
	out := make(map[string]toolDefinition, len(items))
	for _, item := range items {
		out[item.Name] = item
	}
	return out, nil
}

func (s *server) registerClaimTools(ms *mcp.Server) {
	definitions, err := legacyDefinitions()
	if err != nil {
		panic(err)
	}
	for _, name := range []string{"tenant_capabilities_list", "tenant_claim_plan", "tenant_claim_create", "tenant_claim_get", "tenant_claim_list", "tenant_claim_update", "tenant_claim_renew", "tenant_claim_cancel", "tenant_usage_summary_get"} {
		definition := definitions[name]
		if definition.Name == "" {
			panic("missing tenant schema: " + name)
		}
		mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations(name), Name: name, Description: definition.Description, InputSchema: definition.InputSchema},
			func(ctx context.Context, _ *mcp.CallToolRequest, args map[string]any) (*mcp.CallToolResult, any, error) {
				id, err := identityFrom(ctx)
				if err != nil {
					return nil, nil, err
				}
				if name != "tenant_capabilities_list" && name != "tenant_claim_get" && name != "tenant_claim_list" && name != "tenant_usage_summary_get" {
					if err := requireOperator(id); err != nil {
						return nil, nil, err
					}
				}
				value, err := s.callClaim(ctx, id, name, args)
				return nil, value, err
			})
	}
}

func (s *server) callClaim(ctx context.Context, id identity, name string, args map[string]any) (any, error) {
	tenant := id.Tenant
	value := func(key string) string { result, _ := args[key].(string); return result }
	if name == "tenant_claim_plan" || name == "tenant_claim_create" {
		capability := asString(object(object(args["claim"])["spec"]), "capability")
		if err := requireGrantedConsumable(ctx, id, capability); err != nil {
			return nil, err
		}
	}
	if name == "tenant_capabilities_list" {
		grants, err := tenantGrantedConsumables(ctx, tenant, id.Role)
		if err != nil {
			return nil, err
		}
		_, checks := s.readiness(ctx)
		result := map[string]any{"tenantRef": tenant, "capabilities": []map[string]any{
			{"name": "kubernetes.namespace.v1", "access": "native", "approvalRequired": false, "imagePolicy": "tenant-selected", "interfaces": []string{"Kubernetes API", "Helm", "GitOps"}},
			{"name": "compute.workload.v1", "access": "optional-managed-claim", "kinds": []string{"Job", "Deployment"}, "resources": []string{"cpu", "memory", "ephemeralStorage"}, "publicExposure": "shared-service-opt-in", "imagePolicy": "managed claims use the optional supply-chain policy", "ttl": map[string]any{"required": true, "renewable": true, "minimumSeconds": 300, "maximumSeconds": 2592000}},
			{"name": "storage.volume.v1", "plans": []string{"rbd", "cephfs"}, "lifecycle": "independent-retained", "ttl": map[string]any{"required": true, "renewable": true, "minimumSeconds": 300, "maximumSeconds": 2592000}},
		}, "grantedConsumables": grants, "requiresApproval": false, "operationalReadiness": checks, "pricingIncluded": false}
		// Keep the long-standing discovery tool useful for clients whose MCP tool
		// snapshot predates tenant_self_service_capabilities. A namespace lookup
		// failure must not hide the already-known grant and claim information.
		if namespaces, err := controlRequest(ctx, tenant, "/v1/namespaces"); err == nil {
			result["selfService"] = selfServiceCapabilitiesFromState(id, namespaces, grants)
			result["selfServiceStatus"] = "ready"
		} else {
			result["selfServiceStatus"] = "unavailable"
		}
		return result, nil
	}
	if name == "tenant_usage_summary_get" {
		return s.claimUsageSummary(ctx, tenant, value("claim_name"))
	}
	path := "/v1/claims"
	if name != "tenant_claim_plan" && name != "tenant_claim_create" && name != "tenant_claim_list" {
		if value("name") == "" {
			return nil, errors.New("claim name is required")
		}
		path += "/" + url.PathEscape(value("name"))
	}
	switch name {
	case "tenant_claim_plan":
		return controlRequestBody(ctx, tenant, path+"/plan", map[string]any{"claim": args["claim"]})
	case "tenant_claim_create":
		return controlRequestBody(ctx, tenant, path, map[string]any{"claim": args["claim"], "claimHash": args["claim_hash"]})
	case "tenant_claim_get", "tenant_claim_list":
		return controlRequest(ctx, tenant, path)
	case "tenant_claim_update":
		return controlRequestBody(ctx, tenant, path+"/update", map[string]any{"claim": args["claim"], "claimHash": args["claim_hash"], "idempotencyKey": args["idempotency_key"]})
	case "tenant_claim_renew":
		return controlRequestBody(ctx, tenant, path+"/renew", map[string]any{"ttlSeconds": args["ttl_seconds"], "idempotencyKey": args["idempotency_key"]})
	case "tenant_claim_cancel":
		return controlRequestBody(ctx, tenant, path+"/cancel", map[string]any{"idempotencyKey": args["idempotency_key"]})
	default:
		return nil, errors.New("tool is not published")
	}
}

func object(value any) map[string]any {
	result, _ := value.(map[string]any)
	if result == nil {
		return map[string]any{}
	}
	return result
}

func (s *server) claimUsageSummary(ctx context.Context, tenant, claimName string) (any, error) {
	path := "/v1/claims"
	if claimName != "" {
		path += "/" + url.PathEscape(claimName)
	}
	result, err := controlRequest(ctx, tenant, path)
	if err != nil {
		return nil, err
	}
	items := []any{}
	if claimName == "" {
		items, _ = result["items"].([]any)
	} else {
		items = append(items, result)
	}
	rows := []map[string]any{}
	serviceRows := []map[string]any{}
	anyObserved := false
	for _, raw := range items {
		claim := object(raw)
		spec, status, metadata := object(claim["spec"]), object(claim["status"]), object(claim["metadata"])
		params := object(spec["parameters"])
		row := map[string]any{"claimName": metadata["name"], "claimUid": metadata["uid"], "capability": spec["capability"], "phase": status["phase"], "expiresAt": status["expiresAt"]}
		if spec["capability"] == "storage.volume.v1" {
			row["requested"] = map[string]any{"storageGiB": params["sizeGiB"]}
		} else {
			pods := params["replicas"]
			if pods == nil {
				pods = params["parallelism"]
			}
			if pods == nil {
				pods = 1
			}
			requested := map[string]any{"pods": pods, "publicEndpoints": 0}
			for key, value := range object(params["resources"]) {
				requested[key] = value
			}
			if object(params["exposure"])["enabled"] == true {
				requested["publicEndpoints"] = 1
			}
			row["requested"] = requested
		}
		if uid, ok := metadata["uid"].(string); ok && uid != "" {
			usage, err := s.usageSummary(tenant, uid)
			if err != nil {
				return nil, err
			}
			row["usage"] = map[string]any{"totals": usage["totals"], "eventCount": usage["eventCount"], "latestObservedAt": usage["latestObservedAt"]}
			row["usageState"] = "pending-observation"
			if usage["eventCount"].(int) > 0 {
				row["usageState"] = "observed"
				anyObserved = true
			}
		}
		rows = append(rows, row)
	}
	if claimName == "" {
		services, serviceErr := controlRequest(ctx, tenant, "/v1/services")
		if serviceErr != nil {
			return nil, serviceErr
		}
		for _, raw := range objectSlice(services["items"]) {
			instance := object(raw)
			spec, status, metadata := object(instance["spec"]), object(instance["status"]), object(instance["metadata"])
			name, _ := metadata["name"].(string)
			uid, _ := metadata["uid"].(string)
			resource := "service://" + tenant + "/" + name
			usage, usageErr := s.usageSummary(tenant, resource)
			if usageErr != nil {
				return nil, usageErr
			}
			observed := usage["eventCount"].(int) > 0
			if observed {
				anyObserved = true
			}
			serviceRows = append(serviceRows, map[string]any{
				"instanceName": name, "instanceUid": uid, "resourceRef": resource,
				"serviceClass": spec["serviceClass"], "phase": status["phase"],
				"usage":      map[string]any{"totals": usage["totals"], "eventCount": usage["eventCount"], "latestObservedAt": usage["latestObservedAt"]},
				"usageState": map[bool]string{true: "observed", false: "pending-observation"}[observed],
			})
		}
	}
	state := "pending-observation"
	if anyObserved {
		state = "accepted-events"
	}
	return map[string]any{"tenantRef": tenant, "claims": rows, "serviceInstances": serviceRows, "pricingApplied": false, "accountingPosted": false, "meteringSource": "birfucate", "usageState": state}, nil
}

func objectSlice(value any) []any {
	items, _ := value.([]any)
	return items
}
