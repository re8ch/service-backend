package main

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"regexp"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func (s *server) registerHarborTools(ms *mcp.Server) {
	definitions, err := legacyDefinitions()
	if err != nil {
		panic(err)
	}
	for _, name := range []string{"tenant_human_access_issue", "runtime_image_catalog", "runtime_image_manage", "runtime_image_build"} {
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
				operation := name
				if strings.HasPrefix(name, "runtime_image_") {
					operation, err = runtimeImageOperation(name, args)
					if err != nil {
						return nil, nil, err
					}
				}
				if operation == "harbor_artifact_scan_start" || operation == "harbor_tag_create" || operation == "harbor_tag_delete" || operation == "harbor_build_start" || operation == "harbor_build_cancel" {
					if err := requireOperator(id); err != nil {
						return nil, nil, err
					}
				}
				value, err := s.callHarbor(ctx, id, operation, args)
				return nil, value, err
			})
	}
}

func runtimeImageOperation(name string, args map[string]any) (string, error) {
	action := asString(args, "action")
	require := func(keys ...string) error {
		for _, key := range keys {
			if asString(args, key) == "" {
				return errors.New(key + " is required")
			}
		}
		return nil
	}
	switch name {
	case "runtime_image_catalog":
		operations := map[string]string{
			"get_project":         "harbor_project_get",
			"list_repositories":   "harbor_repository_list",
			"list_artifacts":      "harbor_artifact_list",
			"get_artifact":        "harbor_artifact_get",
			"get_vulnerabilities": "harbor_vulnerability_report_get",
			"get_sbom":            "harbor_sbom_get",
			"list_accessories":    "harbor_accessory_list",
			"list_audit":          "harbor_audit_log_list",
		}
		operation := operations[action]
		if operation == "" {
			break
		}
		if action == "list_artifacts" {
			return operation, require("repository")
		}
		if action == "get_artifact" || action == "get_vulnerabilities" || action == "get_sbom" || action == "list_accessories" {
			return operation, require("repository", "reference")
		}
		return operation, nil
	case "runtime_image_manage":
		operations := map[string]string{"start_scan": "harbor_artifact_scan_start", "create_tag": "harbor_tag_create", "delete_tag": "harbor_tag_delete"}
		operation := operations[action]
		if operation == "" {
			break
		}
		if err := require("repository", "reference"); err != nil {
			return "", err
		}
		if action == "create_tag" || action == "delete_tag" {
			return operation, require("tag")
		}
		return operation, nil
	case "runtime_image_build":
		operations := map[string]string{"start": "harbor_build_start", "get": "harbor_build_get", "list": "harbor_build_list", "cancel": "harbor_build_cancel"}
		operation := operations[action]
		if operation == "" {
			break
		}
		if action == "start" {
			return operation, require("credential_ref", "context", "repository", "tag", "idempotency_key")
		}
		if action == "get" || action == "cancel" {
			return operation, require("operation_id")
		}
		return operation, nil
	}
	return "", errors.New("unsupported runtime-image action")
}

func flag(args map[string]any, key string, defaultValue bool) bool {
	value, ok := args[key].(bool)
	if !ok {
		return defaultValue
	}
	return value
}

func (s *server) callHarbor(ctx context.Context, id identity, name string, args map[string]any) (any, error) {
	tenant := id.Tenant
	reference := asString(args, "credential_ref")
	if name == "tenant_human_access_issue" {
		if err := readyHumanCredential(ctx, tenant, reference); err != nil {
			return nil, err
		}
		if _, _, err := credentialMaterial(ctx, tenant, reference); err != nil {
			return nil, err
		}
		issued, err := s.issueRedemption(reference, tenant)
		if err != nil {
			return nil, err
		}
		return map[string]any{"tenantRef": tenant, "credential": issued, "credentialMaterialReturned": false}, nil
	}
	if strings.HasPrefix(name, "harbor_build_") && name != "harbor_build_start" {
		return s.callBuild(ctx, tenant, name, args)
	}
	client, project, err := tenantHarbor(ctx, tenant, reference)
	if err != nil {
		return nil, err
	}
	projectPath := "/projects/" + url.PathEscape(project)
	repository := asString(args, "repository")
	ref := asString(args, "reference")
	repositoryPath := projectPath + "/repositories/" + url.PathEscape(repository)
	artifactPath := harborArtifactPath(project, repository, ref)
	switch name {
	case "harbor_project_get":
		value, err := client.request(ctx, http.MethodGet, projectPath, nil, true)
		if err == nil {
			return map[string]any{"tenant": tenant, "project": value}, nil
		}
		// Project-scoped robots can enumerate their repositories but Harbor may
		// reject the administrator-oriented project metadata endpoint.
		repositories, scopedErr := client.paged(ctx, projectPath+"/repositories")
		if scopedErr != nil {
			return nil, err
		}
		return map[string]any{"tenant": tenant, "project": map[string]any{
			"name": project, "metadataAvailable": false, "repositoryCount": len(repositories),
		}}, nil
	case "harbor_repository_list":
		value, err := client.paged(ctx, projectPath+"/repositories")
		return map[string]any{"tenant": tenant, "project": project, "repositories": value}, err
	case "harbor_artifact_list":
		value, err := client.paged(ctx, repositoryPath+"/artifacts?with_tag=true&with_scan_overview=true")
		return map[string]any{"tenant": tenant, "project": project, "repository": repository, "artifacts": value}, err
	case "harbor_artifact_get":
		return client.request(ctx, http.MethodGet, artifactPath+"?with_tag=true&with_scan_overview=true&with_accessory=true", nil, true)
	case "harbor_vulnerability_report_get":
		return client.request(ctx, http.MethodGet, artifactPath+"/additions/vulnerabilities", nil, true)
	case "harbor_sbom_get":
		return client.request(ctx, http.MethodGet, artifactPath+"/additions/sbom", nil, true)
	case "harbor_accessory_list":
		value, err := client.paged(ctx, artifactPath+"/accessories")
		return map[string]any{"accessories": value}, err
	case "harbor_audit_log_list":
		value, err := client.paged(ctx, "/audit-logs?q="+url.QueryEscape("project_name="+project))
		return map[string]any{"events": value}, err
	case "harbor_artifact_scan_start":
		preview := mutationPreview("harbor/"+project+"/"+repository+"@"+ref, nil, map[string]any{"scan": "requested"}, flag(args, "apply", false), "scan requests are append-only")
		if flag(args, "apply", false) {
			_, err := client.request(ctx, http.MethodPost, artifactPath+"/scan", nil, true)
			if err != nil {
				return nil, err
			}
		}
		return preview, nil
	case "harbor_tag_create", "harbor_tag_delete":
		present, tag := name == "harbor_tag_create", asString(args, "tag")
		var before any
		if !present {
			before = map[string]any{"present": true}
		}
		preview := mutationPreview("harbor/"+project+"/"+repository+"@"+ref+"/tags/"+tag, before, map[string]any{"present": present}, flag(args, "apply", false), map[string]any{"present": !present})
		if flag(args, "apply", false) {
			if present {
				err = client.createTag(ctx, project, repository, ref, tag)
			} else {
				err = client.deleteTag(ctx, project, repository, ref, tag)
			}
			if err != nil {
				return nil, err
			}
		}
		return preview, nil
	case "harbor_build_start":
		// tenantHarbor already validates the tenant-scoped credential and project.
		// A build-push robot cannot read project metadata, and the worker checks
		// its repository permissions when it pushes the image.
		preview := mutationPreview("harbor/"+project+"/"+repository+":"+asString(args, "tag"), nil, map[string]any{"sbom": true, "provenance": "mode=max", "sign": flag(args, "sign", true), "scan": flag(args, "scan", true)}, flag(args, "apply", false), "cancel build and explicitly delete its artifact")
		if flag(args, "apply", false) {
			result, err := startBuild(ctx, tenant, project, args)
			if err != nil {
				return nil, err
			}
			for key, value := range result {
				preview[key] = value
			}
		}
		return preview, nil
	}
	return nil, errors.New("tool is not published")
}

func (s *server) callBuild(ctx context.Context, tenant, name string, args map[string]any) (any, error) {
	base := "/v1/tenants/" + url.PathEscape(tenant) + "/builds"
	switch name {
	case "harbor_build_get":
		return supplyChainRequest(ctx, http.MethodGet, base+"/"+url.PathEscape(asString(args, "operation_id")), nil)
	case "harbor_build_list":
		return supplyChainRequest(ctx, http.MethodGet, base, nil)
	case "harbor_build_cancel":
		id := asString(args, "operation_id")
		preview := mutationPreview("build/"+tenant+"/"+id, map[string]any{"state": "running"}, map[string]any{"state": "cancelled"}, flag(args, "apply", false), "restart build")
		if flag(args, "apply", false) {
			result, err := supplyChainRequest(ctx, http.MethodPost, base+"/"+url.PathEscape(id)+"/cancel", map[string]any{})
			if err != nil {
				return nil, err
			}
			for key, value := range result {
				preview[key] = value
			}
		}
		return preview, nil
	}
	return nil, errors.New("tool is not published")
}

var repositoryPattern = regexp.MustCompile(`^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$`)
var tagPattern = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$`)
var dockerfilePattern = regexp.MustCompile(`^[A-Za-z0-9_./-]{1,256}$`)

func startBuild(ctx context.Context, tenant, project string, args map[string]any) (map[string]any, error) {
	repository, tag := asString(args, "repository"), asString(args, "tag")
	if !repositoryPattern.MatchString(repository) || !tagPattern.MatchString(tag) {
		return nil, errors.New("invalid OCI repository or tag")
	}
	dockerfile := asString(args, "dockerfile")
	if dockerfile == "" {
		dockerfile = "Dockerfile"
	}
	if !dockerfilePattern.MatchString(dockerfile) {
		return nil, errors.New("invalid Dockerfile path")
	}
	for _, part := range strings.Split(dockerfile, "/") {
		if part == ".." {
			return nil, errors.New("invalid Dockerfile path")
		}
	}
	contextURL := asString(args, "context")
	if !strings.HasPrefix(contextURL, "https://") {
		return nil, errors.New("build context must be an HTTPS source reference")
	}
	platforms := args["platforms"]
	if len(slice(platforms)) == 0 {
		platforms = []string{"linux/amd64"}
	}
	payload := map[string]any{"tenant": tenant, "project": project, "target": project + "/" + repository + ":" + tag, "context": contextURL, "revision": asString(args, "revision"), "dockerfile": dockerfile, "platforms": platforms, "buildArgs": object(args["build_args"]), "credentialRef": args["credential_ref"], "attestations": map[string]any{"sbom": true, "provenance": "mode=max"}, "sign": flag(args, "sign", true), "scan": flag(args, "scan", true), "idempotencyKey": args["idempotency_key"]}
	return supplyChainRequest(ctx, http.MethodPost, "/v1/builds", payload)
}
