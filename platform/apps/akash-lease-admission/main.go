package main

import (
	"crypto/tls"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
)

type admissionReview struct {
	APIVersion string             `json:"apiVersion"`
	Kind       string             `json:"kind"`
	Request    *admissionRequest  `json:"request,omitempty"`
	Response   *admissionResponse `json:"response,omitempty"`
}

type admissionRequest struct {
	UID       string          `json:"uid"`
	Namespace string          `json:"namespace"`
	Resource  groupResource   `json:"resource"`
	Object    json.RawMessage `json:"object"`
}

type groupResource struct {
	Resource string `json:"resource"`
}

type admissionResponse struct {
	UID       string   `json:"uid"`
	Allowed   bool     `json:"allowed"`
	Status    *status  `json:"status,omitempty"`
	Patch     []byte   `json:"patch,omitempty"`
	PatchType *string  `json:"patchType,omitempty"`
	Warnings  []string `json:"warnings,omitempty"`
}

type status struct {
	Message string `json:"message"`
}
type patchOperation struct {
	Op, Path string
	Value    any
}

func (p patchOperation) MarshalJSON() ([]byte, error) {
	return json.Marshal(map[string]any{"op": p.Op, "path": p.Path, "value": p.Value})
}

func deny(uid, message string) admissionReview {
	return admissionReview{APIVersion: "admission.k8s.io/v1", Kind: "AdmissionReview", Response: &admissionResponse{UID: uid, Allowed: false, Status: &status{Message: message}}}
}

func allowed(uid string, patch []patchOperation) admissionReview {
	response := &admissionResponse{UID: uid, Allowed: true}
	if len(patch) > 0 {
		data, _ := json.Marshal(patch)
		patchType := "JSONPatch"
		response.Patch, response.PatchType = data, &patchType
	}
	return admissionReview{APIVersion: "admission.k8s.io/v1", Kind: "AdmissionReview", Response: response}
}

func boolAt(m map[string]any, key string) bool { value, _ := m[key].(bool); return value }

func validateContainer(container map[string]any) error {
	security, _ := container["securityContext"].(map[string]any)
	if boolAt(security, "privileged") || boolAt(security, "allowPrivilegeEscalation") {
		return fmt.Errorf("privileged containers and privilege escalation are forbidden")
	}
	if capabilities, ok := security["capabilities"].(map[string]any); ok {
		if add, ok := capabilities["add"].([]any); ok && len(add) > 0 {
			return fmt.Errorf("adding Linux capabilities is forbidden")
		}
	}
	return nil
}

func mutatePod(raw json.RawMessage) ([]patchOperation, error) {
	var pod map[string]any
	if err := json.Unmarshal(raw, &pod); err != nil {
		return nil, fmt.Errorf("invalid pod: %w", err)
	}
	spec, ok := pod["spec"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("pod spec is missing")
	}
	for _, key := range []string{"hostNetwork", "hostPID", "hostIPC"} {
		if boolAt(spec, key) {
			return nil, fmt.Errorf("%s is forbidden", key)
		}
	}
	if runtimeClass, _ := spec["runtimeClassName"].(string); runtimeClass != "" {
		return nil, fmt.Errorf("custom runtimeClassName is forbidden")
	}
	if nodeName, _ := spec["nodeName"].(string); nodeName != "" && nodeName != "r640" {
		return nil, fmt.Errorf("nodeName must be empty or r640")
	}
	for _, rawVolume := range asSlice(spec["volumes"]) {
		volume, _ := rawVolume.(map[string]any)
		if _, found := volume["hostPath"]; found {
			return nil, fmt.Errorf("hostPath volumes are forbidden")
		}
	}
	for _, field := range []string{"initContainers", "containers", "ephemeralContainers"} {
		for _, rawContainer := range asSlice(spec[field]) {
			container, _ := rawContainer.(map[string]any)
			if err := validateContainer(container); err != nil {
				return nil, err
			}
		}
	}

	affinity, _ := spec["affinity"].(map[string]any)
	if affinity == nil {
		affinity = map[string]any{}
	}
	nodeAffinity, _ := affinity["nodeAffinity"].(map[string]any)
	if nodeAffinity == nil {
		nodeAffinity = map[string]any{}
	}
	required, _ := nodeAffinity["requiredDuringSchedulingIgnoredDuringExecution"].(map[string]any)
	if required == nil {
		required = map[string]any{}
	}
	terms := asSlice(required["nodeSelectorTerms"])
	if len(terms) == 0 {
		terms = []any{map[string]any{}}
	}
	for _, rawTerm := range terms {
		term, _ := rawTerm.(map[string]any)
		if term == nil {
			return nil, fmt.Errorf("invalid node selector term")
		}
		expressions := asSlice(term["matchExpressions"])
		expressions = append(expressions, map[string]any{"key": "kubernetes.io/hostname", "operator": "In", "values": []any{"r640"}})
		term["matchExpressions"] = expressions
	}
	required["nodeSelectorTerms"] = terms
	nodeAffinity["requiredDuringSchedulingIgnoredDuringExecution"] = required
	affinity["nodeAffinity"] = nodeAffinity

	tolerations := asSlice(spec["tolerations"])
	foundToleration := false
	for _, rawToleration := range tolerations {
		toleration, _ := rawToleration.(map[string]any)
		if toleration["key"] == "dedicated" && toleration["value"] == "pg-re8ch" && toleration["effect"] == "NoSchedule" {
			foundToleration = true
		}
	}
	if !foundToleration {
		tolerations = append(tolerations, map[string]any{"key": "dedicated", "operator": "Equal", "value": "pg-re8ch", "effect": "NoSchedule"})
	}
	return []patchOperation{
		{Op: pathOp(spec, "affinity"), Path: "/spec/affinity", Value: affinity},
		{Op: pathOp(spec, "tolerations"), Path: "/spec/tolerations", Value: tolerations},
	}, nil
}

func asSlice(value any) []any {
	if result, ok := value.([]any); ok {
		return result
	}
	return []any{}
}
func pathOp(parent map[string]any, key string) string {
	if _, exists := parent[key]; exists {
		return "replace"
	}
	return "add"
}

func admissionHandler(w http.ResponseWriter, r *http.Request) {
	defer r.Body.Close()
	var review admissionReview
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2<<20)).Decode(&review); err != nil {
		http.Error(w, "invalid AdmissionReview", http.StatusBadRequest)
		return
	}
	if review.Request == nil {
		http.Error(w, "missing request", http.StatusBadRequest)
		return
	}
	request := review.Request
	response := deny(request.UID, "only Pod admissions are supported")
	if request.Resource.Resource == "pods" {
		patch, err := mutatePod(request.Object)
		if err != nil {
			response = deny(request.UID, err.Error())
		} else {
			response = allowed(request.UID, patch)
		}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(response)
}

func main() {
	certFile, keyFile := os.Getenv("TLS_CERT_FILE"), os.Getenv("TLS_KEY_FILE")
	if certFile == "" {
		certFile = "/tls/tls.crt"
	}
	if keyFile == "" {
		keyFile = "/tls/tls.key"
	}
	server := &http.Server{Addr: ":8443", Handler: http.HandlerFunc(admissionHandler), TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
	log.Printf("akash lease admission listening on %s", server.Addr)
	log.Fatal(server.ListenAndServeTLS(certFile, keyFile))
}
