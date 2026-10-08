package main

import (
	"context"
	"errors"
	"net/url"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var providerBacked = map[string]bool{"network.among-clusters.v1": true, "registry.harbor.v1": true, "observability.grafana.v1": true}

func slice(value any) []any { result, _ := value.([]any); return result }

func requireOperator(id identity) error {
	if id.Role != "operator" && id.Role != "admin" {
		return errors.New("an operator tenant role is required")
	}
	return nil
}

func (s *server) registerServiceTools(ms *mcp.Server) {
	definitions, err := legacyDefinitions()
	if err != nil {
		panic(err)
	}
	for _, name := range []string{"service_catalog_list", "service_instance_plan", "service_instance_create", "service_instance_get", "service_instance_list", "service_instance_update", "service_instance_suspend", "service_instance_resume", "service_instance_delete", "service_binding_create", "service_binding_list", "service_binding_revoke", "usage_summary_get", "usage_events_list", "operation_get"} {
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
				value, err := s.callService(ctx, id, name, args)
				return nil, value, err
			})
	}
}

func fallbackControl(err error) bool {
	var failure controlHTTPError
	return errors.As(err, &failure) && failure.Status != 401 && failure.Status != 403
}

func (s *server) callService(ctx context.Context, id identity, name string, args map[string]any) (any, error) {
	tenant := id.Tenant
	ref := asString(args, "resource_ref")
	switch name {
	case "service_catalog_list":
		catalog, err := serviceCatalog()
		if err != nil {
			return nil, err
		}
		services := catalog
		if tenant != "artchais" {
			filtered := []map[string]any{}
			for _, service := range services {
				if service["serviceClass"] != "network.among-clusters.v1" {
					filtered = append(filtered, service)
				}
			}
			services = filtered
		}
		if tenant == "artchais" {
			if _, err = controlRequest(ctx, tenant, "/v1/services/catalog"); err != nil {
				var denied controlHTTPError
				if errors.As(err, &denied) && (denied.Status == 401 || denied.Status == 403) {
					filtered := []map[string]any{}
					for _, service := range services {
						if service["serviceClass"] != "network.among-clusters.v1" {
							filtered = append(filtered, service)
						}
					}
					services = filtered
				} else {
					return nil, err
				}
			}
		}
		return map[string]any{"tenantRef": tenant, "services": services, "pricingIncluded": false}, nil
	case "service_instance_plan", "service_instance_create":
		spec := object(args["spec"])
		serviceClass := asString(spec, "serviceClass")
		if err := requireGrantedConsumable(ctx, id, serviceClass); err != nil {
			return nil, err
		}
		if providerBacked[serviceClass] {
			if err := requireOperator(id); err != nil {
				return nil, err
			}
			payload := map[string]any{"name": args["name"], "spec": spec}
			path := "/v1/services/plan"
			if name == "service_instance_create" {
				path = "/v1/services"
				payload["idempotencyKey"] = args["idempotency_key"]
			}
			return controlRequestBody(ctx, tenant, path, payload)
		}
		if _, err := catalogEntry(serviceClass); err != nil {
			return nil, errors.New("service class is not published")
		}
		if err := requireOperator(id); err != nil {
			return nil, err
		}
		if name == "service_instance_plan" {
			return s.instancePlan(tenant, asString(args, "name"), spec)
		}
		return s.instanceMutate(tenant, asString(args, "name"), spec, asString(args, "idempotency_key"), "create")
	case "service_instance_get":
		if candidate, err := resourceName(tenant, ref); err == nil {
			value, err := controlRequest(ctx, tenant, "/v1/services/"+url.PathEscape(candidate))
			if err == nil {
				return value, nil
			}
			if !fallbackControl(err) {
				return nil, err
			}
		}
		return s.instanceGet(tenant, ref)
	case "service_instance_list":
		controlled, err := controlRequest(ctx, tenant, "/v1/services")
		if err != nil {
			return nil, err
		}
		local, err := s.instanceList(tenant)
		if err != nil {
			return nil, err
		}
		combined := []any{}
		for _, item := range local {
			combined = append(combined, item)
		}
		combined = append(combined, slice(controlled["items"])...)
		return map[string]any{"tenantRef": tenant, "instances": combined}, nil
	case "service_instance_update", "service_instance_suspend", "service_instance_resume", "service_instance_delete":
		if err := requireOperator(id); err != nil {
			return nil, err
		}
		if candidate, err := resourceName(tenant, ref); err == nil {
			current, err := controlRequest(ctx, tenant, "/v1/services/"+url.PathEscape(candidate))
			if err == nil && providerBacked[asString(object(current["spec"]), "serviceClass")] {
				if name == "service_instance_update" {
					return nil, errors.New("provider-backed tenant service parameters are immutable")
				}
				desired := map[string]string{"service_instance_suspend": "Suspended", "service_instance_resume": "Ready", "service_instance_delete": "Deleted"}[name]
				return controlRequestBody(ctx, tenant, "/v1/services/"+url.PathEscape(candidate)+"/lifecycle", map[string]any{"desiredState": desired, "idempotencyKey": args["idempotency_key"]})
			}
			if err != nil && !fallbackControl(err) {
				return nil, err
			}
		}
		current, err := s.instanceGet(tenant, ref)
		if err != nil {
			return nil, err
		}
		spec := object(args["spec"])
		if len(spec) == 0 {
			spec = object(current["spec"])
		}
		if args["spec"] != nil && asString(spec, "serviceClass") != asString(current, "service_class") {
			return nil, errors.New("serviceClass is immutable")
		}
		if name != "service_instance_update" {
			specCopy := map[string]any{}
			for key, value := range spec {
				specCopy[key] = value
			}
			spec = specCopy
			spec["desiredState"] = map[string]string{"service_instance_suspend": "Suspended", "service_instance_resume": "Ready", "service_instance_delete": "Deleted"}[name]
		}
		return s.instanceMutate(tenant, asString(current, "name"), spec, asString(args, "idempotency_key"), strings.TrimPrefix(name, "service_instance_"))
	case "service_binding_create":
		_, err := s.instanceGet(tenant, ref)
		if err != nil {
			return nil, err
		}
		if err = requireOperator(id); err != nil {
			return nil, err
		}
		return s.bindingCreate(tenant, ref, asString(args, "binding_type"), object(args["parameters"]))
	case "service_binding_list":
		items, err := s.bindingList(tenant, ref)
		return map[string]any{"tenantRef": tenant, "bindings": items}, err
	case "service_binding_revoke":
		if err := requireOperator(id); err != nil {
			return nil, err
		}
		return s.bindingRevoke(tenant, asString(args, "binding_ref"))
	case "usage_summary_get":
		return s.usageSummary(tenant, ref)
	case "usage_events_list":
		limit := 100
		if value, ok := args["limit"]; ok {
			limit = intValue(value)
		}
		return s.usageEvents(tenant, ref, limit)
	case "operation_get":
		return s.operationGet(tenant, asString(args, "operation_id"))
	}
	return nil, errors.New("tool is not published")
}
