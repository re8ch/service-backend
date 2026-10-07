package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAgentReceiptIsBodyFreeAndSecretarySigned(t *testing.T) {
	s := testServer(t)
	const secret = "secretary-test-key"
	seen := false
	secretary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		if strings.Contains(string(raw), "private task text") {
			t.Error("message body leaked to Secretary")
		}
		mac := hmac.New(sha256.New, []byte(secret))
		mac.Write(append([]byte(r.Header.Get("X-Secretary-Timestamp")+"."), raw...))
		if !hmac.Equal([]byte(hex.EncodeToString(mac.Sum(nil))), []byte(r.Header.Get("X-Secretary-Signature"))) {
			t.Error("invalid Secretary signature")
		}
		seen = true
		w.WriteHeader(http.StatusAccepted)
	}))
	defer secretary.Close()
	t.Setenv("SECRETARY_OBSERVATION_URL", secretary.URL)
	t.Setenv("SECRETARY_WEBHOOK_SECRET", secret)
	if err := recordA2AReceipt(context.Background(), s.db, "task_123", "acme", "requester", "agent_123", "queued", "private task text"); err != nil {
		t.Fatal(err)
	}
	if state := flushA2AReceipt(context.Background(), s.db, "task_123", "queued"); state != "accepted" || !seen {
		t.Fatalf("Secretary receipt: %s seen=%v", state, seen)
	}
}

func TestAgentA2ARequestReplyAndOwnership(t *testing.T) {
	s := testServer(t)
	goTrue := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims, err := decodeClaims(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		if err != nil {
			writeJSON(w, 401, map[string]string{"error": "invalid_token"})
			return
		}
		writeJSON(w, 200, map[string]string{"id": claims.Subject})
	}))
	defer goTrue.Close()
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"tenantRef": "acme", "role": "operator", "defaultNamespace": "acme"})
	}))
	defer control.Close()
	t.Setenv("SUPABASE_USER_URL", goTrue.URL)
	t.Setenv("TENANT_CONTROL_URL", control.URL)
	t.Setenv("TENANT_CLAIM_SERVICE_TOKEN", "test-service-token")
	call := func(subject, name string, args map[string]any) map[string]any {
		t.Helper()
		body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": map[string]any{"name": name, "arguments": args}})
		req := httptest.NewRequest("POST", "/mcp", strings.NewReader(string(body)))
		req.Header.Set("Authorization", "Bearer "+fixtureTokenFor(subject))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		s.handler().ServeHTTP(response, req)
		if response.Code != 200 {
			t.Fatalf("%s: HTTP %d %s", name, response.Code, response.Body.String())
		}
		var decoded map[string]any
		if err := json.Unmarshal(response.Body.Bytes(), &decoded); err != nil {
			t.Fatal(err)
		}
		return decoded["result"].(map[string]any)
	}
	content := func(result map[string]any) map[string]any {
		t.Helper()
		if result["isError"] == true {
			t.Fatalf("MCP error: %v", result)
		}
		return result["structuredContent"].(map[string]any)
	}
	agent := content(call("worker", "agent_register", map[string]any{"name": "Test worker", "capabilities": "Answer test tasks", "leaseSeconds": 300}))["agentRef"].(string)
	listed := content(call("requester", "agent_list", map[string]any{}))["agents"].([]any)
	if len(listed) != 1 {
		t.Fatalf("running agent not visible: %v", listed)
	}
	task := content(call("requester", "agent_task_send", map[string]any{"agentRef": agent, "message": "Return pong", "idempotencyKey": "test-ping-001"}))["taskRef"].(string)
	if content(call("requester", "agent_task_send", map[string]any{"agentRef": agent, "message": "Return pong", "idempotencyKey": "test-ping-001"}))["taskRef"] != task {
		t.Fatal("idempotent send created a second task")
	}
	if call("other", "agent_task_poll", map[string]any{"agentRef": agent})["isError"] != true {
		t.Fatal("another user polled agent inbox")
	}
	if call("other", "agent_task_get", map[string]any{"taskRef": task})["isError"] != true {
		t.Fatal("another user read task")
	}
	received := content(call("worker", "agent_task_poll", map[string]any{"agentRef": agent}))["task"].(map[string]any)
	if received["taskRef"] != task || received["message"] != "Return pong" {
		t.Fatalf("wrong task: %v", received)
	}
	if _, err := s.db.Exec(`UPDATE a2a_tasks SET updated_at=0 WHERE ref=?`, task); err != nil {
		t.Fatal(err)
	}
	recovered := content(call("worker", "agent_task_poll", map[string]any{"agentRef": agent}))["task"].(map[string]any)
	if recovered["taskRef"] != task {
		t.Fatalf("unanswered task was not recoverable: %v", recovered)
	}
	content(call("worker", "agent_task_reply", map[string]any{"taskRef": task, "reply": "pong"}))
	content(call("worker", "agent_task_reply", map[string]any{"taskRef": task, "reply": "pong"}))
	result := content(call("requester", "agent_task_get", map[string]any{"taskRef": task}))
	if result["status"] != "completed" || result["reply"] != "pong" {
		t.Fatalf("wrong reply: %v", result)
	}
}
