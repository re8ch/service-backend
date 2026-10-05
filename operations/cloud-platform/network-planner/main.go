package main

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"
)

//go:embed operations.json
var assets embed.FS

var dnsName = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$`)
var hashValue = regexp.MustCompile(`^[0-9a-f]{64}$`)

type Operation struct {
	Name         string `json:"name"`
	Method       string `json:"method"`
	Path         string `json:"path"`
	GroupVersion string `json:"groupVersion"`
	Kind         string `json:"kind"`
	SchemaHash   string `json:"schemaHash"`
}

type Input struct {
	Tool           string          `json:"tool"`
	Name           string          `json:"name"`
	Namespace      string          `json:"namespace"`
	IdempotencyKey string          `json:"idempotencyKey"`
	Body           json.RawMessage `json:"body"`
	Existing       json.RawMessage `json:"existing"`
}

type Output struct {
	Method          string          `json:"method"`
	Path            string          `json:"path"`
	Body            json.RawMessage `json:"body,omitempty"`
	PlanHash        string          `json:"planHash"`
	UID             string          `json:"uid,omitempty"`
	ResourceVersion string          `json:"resourceVersion,omitempty"`
}

func object(data json.RawMessage) (map[string]any, error) {
	var value map[string]any
	if err := json.Unmarshal(data, &value); err != nil || value == nil {
		return nil, errors.New("expected a JSON object")
	}
	return value, nil
}
func nested(value map[string]any, key string) map[string]any {
	result, _ := value[key].(map[string]any)
	return result
}
func stringField(value map[string]any, key string) string {
	result, _ := value[key].(string)
	return result
}
func validSegment(value string) bool {
	return dnsName.MatchString(value) && !strings.Contains(value, "..")
}

func plan(op Operation, in Input) (Output, error) {
	if !validSegment(in.Name) || len(in.IdempotencyKey) < 8 || len(in.IdempotencyKey) > 128 {
		return Output{}, errors.New("invalid resource name or idempotency key")
	}
	path := op.Path
	if strings.Contains(path, "{namespace}") {
		if !validSegment(in.Namespace) {
			return Output{}, errors.New("invalid namespace")
		}
		path = strings.ReplaceAll(path, "{namespace}", in.Namespace)
	} else if in.Namespace != "" {
		return Output{}, errors.New("unexpected namespace")
	}
	path = strings.ReplaceAll(path, "{name}", in.Name)
	if strings.ContainsAny(path, "{}?#") || !strings.HasPrefix(path, "/"+op.GroupVersion+"/") {
		return Output{}, errors.New("unbound native path")
	}
	out := Output{Method: op.Method, Path: path}
	if op.Method == "DELETE" {
		if len(in.Body) != 0 {
			return Output{}, errors.New("delete accepts no resource body")
		}
	} else {
		body, err := object(in.Body)
		if err != nil {
			return Output{}, err
		}
		metadata := nested(body, "metadata")
		if stringField(body, "apiVersion") != strings.TrimPrefix(op.GroupVersion, "apis/") || stringField(body, "kind") != op.Kind || stringField(metadata, "name") != in.Name || stringField(metadata, "namespace") != in.Namespace {
			return Output{}, errors.New("resource body target differs from native operation")
		}
		if op.Method == "POST" {
			if in.Existing != nil {
				return Output{}, errors.New("resource already exists")
			}
			marker := sha256.Sum256([]byte(in.IdempotencyKey))
			annotations := nested(metadata, "annotations")
			if annotations == nil {
				annotations = map[string]any{}
				metadata["annotations"] = annotations
			}
			annotations["operations.re8ch.com/idempotency-hash"] = hex.EncodeToString(marker[:])
		}
		encoded, err := json.Marshal(body)
		if err != nil {
			return Output{}, err
		}
		out.Body = encoded
	}
	if op.Method != "POST" {
		current, err := object(in.Existing)
		if err != nil {
			return Output{}, errors.New("target must exist")
		}
		if stringField(current, "apiVersion") != strings.TrimPrefix(op.GroupVersion, "apis/") || stringField(current, "kind") != op.Kind {
			return Output{}, errors.New("target kind or apiVersion changed")
		}
		metadata := nested(current, "metadata")
		out.UID, out.ResourceVersion = stringField(metadata, "uid"), stringField(metadata, "resourceVersion")
		if out.UID == "" || out.ResourceVersion == "" || stringField(metadata, "name") != in.Name || stringField(metadata, "namespace") != in.Namespace {
			return Output{}, errors.New("target identity is incomplete or changed")
		}
		if op.Method == "PUT" || op.Method == "PATCH" {
			body, _ := object(out.Body)
			bodyMeta := nested(body, "metadata")
			if stringField(bodyMeta, "uid") != out.UID || stringField(bodyMeta, "resourceVersion") != out.ResourceVersion {
				return Output{}, errors.New("body must contain current UID and resourceVersion")
			}
		}
		if op.Method == "DELETE" {
			options := map[string]any{"apiVersion": "meta.k8s.io/v1", "kind": "DeleteOptions", "preconditions": map[string]string{"uid": out.UID, "resourceVersion": out.ResourceVersion}}
			out.Body, _ = json.Marshal(options)
		}
	}
	canonical, _ := json.Marshal([]any{op.SchemaHash, op.Method, path, out.UID, out.ResourceVersion, json.RawMessage(out.Body), in.IdempotencyKey})
	digest := sha256.Sum256(canonical)
	out.PlanHash = hex.EncodeToString(digest[:])
	if !hashValue.MatchString(op.SchemaHash) {
		return Output{}, errors.New("invalid captured schema hash")
	}
	return out, nil
}

func main() {
	fail := func(err error) { fmt.Fprintln(os.Stderr, err); os.Exit(2) }
	data, err := assets.ReadFile("operations.json")
	if err != nil {
		fail(err)
	}
	var operations []Operation
	if err := json.Unmarshal(data, &operations); err != nil {
		fail(err)
	}
	raw, err := io.ReadAll(io.LimitReader(os.Stdin, 4<<20))
	if err != nil {
		fail(err)
	}
	var input Input
	if err := json.Unmarshal(raw, &input); err != nil {
		fail(err)
	}
	var selected *Operation
	for i := range operations {
		if operations[i].Name == input.Tool {
			selected = &operations[i]
			break
		}
	}
	if selected == nil {
		fail(errors.New("operation not in fixed manifest"))
	}
	output, err := plan(*selected, input)
	if err != nil {
		fail(err)
	}
	if err := json.NewEncoder(os.Stdout).Encode(output); err != nil {
		fail(err)
	}
}
