package main

import (
	"context"
	"encoding/json"
	"errors"
	"regexp"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var grafanaResultURI = regexp.MustCompile(`^re8ch://tenant/([^/]+)/grafana/results/(gr_[a-f0-9]+)$`)

func (s *server) registerResources(ms *mcp.Server, id identity) {
	if id.Service || id.Tenant == "" {
		return
	}
	tenant := id.Tenant
	base := "re8ch://tenant/" + tenant + "/"
	descriptors := []struct{ suffix, name string }{
		{"catalog", "Service catalog"}, {"instances", "Service instances"}, {"bindings", "Consumption bindings"}, {"service-usage", "Unpriced service usage summary"},
		{"claims", "Tenant resource claims"}, {"usage", "Unpriced tenant claim showback"}, {"grafana/capabilities", "Grafana capabilities"},
		{"byoc/registrations", "BYOC registrations"}, {"byoc/connections", "BYOC connections"},
	}
	for _, descriptor := range descriptors {
		uri := base + descriptor.suffix
		ms.AddResource(&mcp.Resource{URI: uri, Name: descriptor.name, MIMEType: "application/json"}, func(ctx context.Context, req *mcp.ReadResourceRequest) (*mcp.ReadResourceResult, error) {
			caller, err := identityFrom(ctx)
			if err != nil {
				return nil, err
			}
			if caller.Tenant != tenant || req.Params.URI != uri {
				return nil, mcp.ResourceNotFoundError(req.Params.URI)
			}
			value, err := s.resourceValue(ctx, caller, descriptor.suffix)
			if err != nil {
				return nil, err
			}
			raw, err := json.Marshal(value)
			if err != nil {
				return nil, err
			}
			return &mcp.ReadResourceResult{Contents: []*mcp.ResourceContents{{URI: uri, MIMEType: "application/json", Text: string(raw)}}}, nil
		})
	}
	ms.AddResourceTemplate(&mcp.ResourceTemplate{URITemplate: base + "grafana/results/{resultRef}", Name: "Grafana result"}, func(ctx context.Context, req *mcp.ReadResourceRequest) (*mcp.ReadResourceResult, error) {
		caller, err := identityFrom(ctx)
		if err != nil {
			return nil, err
		}
		match := grafanaResultURI.FindStringSubmatch(req.Params.URI)
		if match == nil || caller.Tenant != tenant || match[1] != tenant {
			return nil, mcp.ResourceNotFoundError(req.Params.URI)
		}
		value, err := s.resultGet(tenant, match[2])
		if err != nil {
			return nil, mcp.ResourceNotFoundError(req.Params.URI)
		}
		if typed, ok := value.(map[string]any); ok {
			if blob, ok := typed["blob"].([]byte); ok {
				mime := asString(typed, "mimeType")
				if mime == "" {
					mime = "application/octet-stream"
				}
				return &mcp.ReadResourceResult{Contents: []*mcp.ResourceContents{{URI: req.Params.URI, MIMEType: mime, Blob: blob}}}, nil
			}
		}
		raw, err := json.Marshal(value)
		if err != nil {
			return nil, err
		}
		return &mcp.ReadResourceResult{Contents: []*mcp.ResourceContents{{URI: req.Params.URI, MIMEType: "application/json", Text: string(raw)}}}, nil
	})
}

func (s *server) resourceValue(ctx context.Context, id identity, suffix string) (any, error) {
	switch suffix {
	case "catalog":
		result, err := s.callService(ctx, id, "service_catalog_list", nil)
		if err != nil {
			return nil, err
		}
		return map[string]any{"services": object(result)["services"], "pricingIncluded": false}, nil
	case "instances":
		result, err := s.callService(ctx, id, "service_instance_list", nil)
		if err != nil {
			return nil, err
		}
		return map[string]any{"instances": object(result)["instances"]}, nil
	case "bindings":
		items, err := s.serviceBindings(ctx, id.Tenant, "")
		return map[string]any{"bindings": items}, err
	case "service-usage":
		return s.usageSummary(id.Tenant, "")
	case "claims":
		return controlRequest(ctx, id.Tenant, "/v1/claims")
	case "usage":
		return s.claimUsageSummary(ctx, id.Tenant, "")
	case "grafana/capabilities":
		return grafanaCapabilities()
	case "byoc/registrations":
		result, err := s.callBYOC(id, "byoc_registration_list", nil)
		return map[string]any{"registrations": object(result)["registrations"]}, err
	case "byoc/connections":
		result, err := s.callBYOC(id, "byoc_connection_list", nil)
		return map[string]any{"connections": object(result)["connections"]}, err
	}
	return nil, errors.New("resource is not visible to the authenticated tenant")
}
