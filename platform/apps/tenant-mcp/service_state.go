package main

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"unicode/utf16"
)

//go:embed service_catalog.json
var serviceCatalogJSON []byte

func serviceCatalog() ([]map[string]any, error) {
	var result []map[string]any
	err := json.Unmarshal(serviceCatalogJSON, &result)
	return result, err
}

func canonicalJSON(value any) ([]byte, error) {
	var buffer strings.Builder
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	var escaped strings.Builder
	for _, character := range strings.TrimSuffix(buffer.String(), "\n") {
		if character <= 127 {
			escaped.WriteRune(character)
			continue
		}
		if character <= 0xffff {
			fmt.Fprintf(&escaped, "\\u%04x", character)
			continue
		}
		first, second := utf16.EncodeRune(character)
		fmt.Fprintf(&escaped, "\\u%04x\\u%04x", first, second)
	}
	return []byte(escaped.String()), nil
}

func canonicalHash(value any) (string, error) {
	raw, err := canonicalJSON(value)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(raw)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

func catalogEntry(serviceClass string) (map[string]any, error) {
	items, err := serviceCatalog()
	if err != nil {
		return nil, err
	}
	for _, item := range items {
		if item["serviceClass"] == serviceClass {
			return item, nil
		}
	}
	return nil, errors.New("serviceClass is not published")
}

func stringMember(values any, item string) bool {
	array, _ := values.([]any)
	for _, raw := range array {
		if raw == item {
			return true
		}
	}
	return false
}

func intValue(value any) int {
	switch typed := value.(type) {
	case float64:
		return int(typed)
	case int:
		return typed
	case json.Number:
		n, _ := typed.Int64()
		return int(n)
	case string:
		n, _ := strconv.Atoi(typed)
		return n
	}
	return 0
}

func forbiddenCredentialField(key string) bool {
	var builder strings.Builder
	for _, character := range strings.ToLower(key) {
		if character >= 'a' && character <= 'z' || character >= '0' && character <= '9' {
			builder.WriteRune(character)
		}
	}
	normalized := builder.String()
	if normalized == "tokenbudget" {
		return false
	}
	for _, marker := range []string{"password", "token", "secret", "kubeconfig", "privatekey", "credential"} {
		if strings.Contains(normalized, marker) {
			return true
		}
	}
	return false
}

func checkedSpec(spec, previous map[string]any) (map[string]any, error) {
	allowed := map[string]bool{"serviceClass": true, "planRef": true, "regionRef": true, "desiredState": true, "parameters": true, "labels": true}
	for key := range spec {
		if !allowed[key] {
			return nil, errors.New("unknown service fields: " + key)
		}
	}
	serviceClass := asString(spec, "serviceClass")
	product, err := catalogEntry(serviceClass)
	if err != nil {
		return nil, err
	}
	if !stringMember(product["plans"], asString(spec, "planRef")) {
		return nil, errors.New("planRef is not published for serviceClass")
	}
	if !stringMember(product["regions"], asString(spec, "regionRef")) {
		return nil, errors.New("regionRef is not published for serviceClass")
	}
	desired := asString(spec, "desiredState")
	if desired == "" {
		desired = "Ready"
	}
	if desired != "Ready" && desired != "Suspended" && desired != "Deleted" {
		return nil, errors.New("invalid desiredState")
	}
	parameters := map[string]any{}
	if value, ok := spec["parameters"]; ok && value != nil {
		parsed, ok := value.(map[string]any)
		if !ok {
			return nil, errors.New("parameters must be an object")
		}
		parameters = parsed
	}
	for key := range parameters {
		if forbiddenCredentialField(key) {
			return nil, errors.New("credential material is forbidden")
		}
		if !stringMember(product["parameters"], key) {
			return nil, errors.New("unknown parameters: " + key)
		}
	}
	switch serviceClass {
	case "storage.volume.v1":
		size := intValue(parameters["sizeGiB"])
		if size < 1 {
			return nil, errors.New("sizeGiB must be positive")
		}
		if previous != nil && size < intValue(object(object(previous["spec"])["parameters"])["sizeGiB"]) {
			return nil, errors.New("volume shrink is not supported")
		}
	case "storage.bucket.v1":
		if visibility := asString(parameters, "visibility"); visibility != "" && visibility != "private" {
			return nil, errors.New("public buckets require a separate network publication binding")
		}
	case "registry.harbor.v1":
		if asString(parameters, "project") == "" || intValue(parameters["quotaGiB"]) < 1 || intValue(parameters["robotDurationDays"]) < 1 || intValue(parameters["robotDurationDays"]) > 365 {
			return nil, errors.New("Harbor requires project, positive quotaGiB and bounded robotDurationDays")
		}
	case "observability.grafana.v1":
		if parameters["datasourceRefs"] == nil || parameters["humanLogin"] == nil || parameters["identityUserRef"] == nil {
			return nil, errors.New("Grafana requires datasourceRefs, humanLogin and identityUserRef")
		}
	case "network.among-clusters.v1":
		if parameters["destinationClusterRefs"] == nil {
			return nil, errors.New("AmongClusters requires destinationClusterRefs")
		}
	}
	copySpec := map[string]any{}
	for key, value := range spec {
		copySpec[key] = value
	}
	copySpec["desiredState"], copySpec["parameters"] = desired, parameters
	if copySpec["labels"] == nil {
		copySpec["labels"] = map[string]any{}
	}
	return copySpec, nil
}

func provisioningIntent(tenant, name string, spec map[string]any) (map[string]any, error) {
	product, err := catalogEntry(asString(spec, "serviceClass"))
	if err != nil {
		return nil, err
	}
	bindings := []string{}
	for _, raw := range product["bindings"].([]any) {
		value := raw.(string)
		value = strings.ReplaceAll(strings.ReplaceAll(value, "{tenant}", tenant), "{name}", name)
		bindings = append(bindings, value)
	}
	return map[string]any{"adapter": product["adapter"], "accessUnit": product["accessUnit"], "bindings": bindings, "resourceRef": "service://" + tenant + "/" + name, "tenantRef": tenant, "desiredState": spec["desiredState"], "credentialMaterialReturned": false}, nil
}
