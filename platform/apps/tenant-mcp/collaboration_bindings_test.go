package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestCollaborationScopesAndPlanOwnership(t *testing.T) {
	t.Setenv("STALWART_URL", "https://provider.example")
	t.Setenv("STALWART_CLIENT_ID", "re8ch-self-service")
	owner := identity{Tenant: "re8ch", Subject: "one"}
	p, err := collaborationPlan(owner, []string{"mail.read", "mail.read", "files.read"})
	if err != nil {
		t.Fatal(err)
	}
	same, _ := collaborationPlan(owner, []string{"files.read", "mail.read"})
	if p["planHash"] != same["planHash"] {
		t.Fatal("scope normalization changed plan")
	}
	other, _ := collaborationPlan(identity{Tenant: "re8ch", Subject: "two"}, []string{"mail.read", "files.read"})
	if p["planHash"] == other["planHash"] {
		t.Fatal("plan transferable between users")
	}
	for _, scopes := range [][]string{{}, {"admin"}, {"calendar.respond"}} {
		if _, _, err := collaborationScopes(scopes); err == nil {
			t.Fatalf("accepted scopes %v", scopes)
		}
	}
	for _, origin := range []string{"http://example.com", "https://user:pass@example.com", "https://example.com/path", "https://example.com?token=x"} {
		t.Setenv("STALWART_URL", origin)
		if _, err := collaborationOrigin(); err == nil {
			t.Fatalf("accepted origin %s", origin)
		}
	}
}

func TestCollaborationConnectionOwnerAndRevocation(t *testing.T) {
	s := testServer(t)
	ctx := context.Background()
	id := identity{Tenant: "one", Subject: "alice"}
	ref := "collaboration://0123456789abcdef0123456789abcdef"
	_, err := s.db.Exec(`INSERT INTO collaboration_connections(ref,tenant,subject,request_hash,scopes,state,account_id,expires) VALUES(?,?,?,?,?,'Ready','account',?)`, ref, id.Tenant, id.Subject, "hash", `["mail.read"]`, time.Now().Unix()+300)
	if err != nil {
		t.Fatal(err)
	}
	for _, other := range []identity{{Tenant: "two", Subject: "alice"}, {Tenant: "one", Subject: "bob"}} {
		if _, err := s.collaborationConnection(ctx, other, ref); err == nil {
			t.Fatal("cross-owner connection found")
		}
	}
	old := collaborationBrokerRequest
	collaborationBrokerRequest = func(context.Context, string, string, any) (map[string]any, error) {
		return nil, errors.New("broker unavailable")
	}
	t.Cleanup(func() { collaborationBrokerRequest = old })
	value, err := s.revokeCollaboration(ctx, id, ref)
	if err != nil || value["state"] != "Revoked" || value["credentialCleanup"] != "pending" {
		t.Fatalf("revocation %v %v", value, err)
	}
	t.Setenv("COLLABORATION_ENABLED", "true")
	if _, err := s.resolveCollaboration(ctx, id, ref); err == nil {
		t.Fatal("revoked credential usable during cleanup failure")
	}
	c, _ := s.collaborationConnection(ctx, id, ref)
	encoded, _ := json.Marshal(c.public())
	for _, sensitive := range []string{"accessToken", "subject", "account_id", "tenant"} {
		if strings.Contains(string(encoded), sensitive) {
			t.Fatalf("leaked %s", sensitive)
		}
	}
}

func TestCollaborationDeviceAuthorizationAndIdempotency(t *testing.T) {
	s := testServer(t)
	ctx := context.Background()
	id := identity{Tenant: "re8ch", Subject: "owner"}
	var origin string
	deviceCalls, tokenCalls := 0, 0
	pending := true
	management := false
	provider := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/auth/device":
			deviceCalls++
			r.ParseForm()
			if r.Form.Get("scope") != "urn:ietf:params:oauth:scope:mail" {
				t.Error("wrong provider scope")
			}
			writeJSON(w, 200, map[string]any{"device_code": "synthetic-device-code", "user_code": "ABCD-EFGH", "verification_uri": origin + "/authorize", "expires_in": 600, "interval": 5})
		case "/auth/token":
			tokenCalls++
			r.ParseForm()
			if r.Form.Get("grant_type") != "urn:ietf:params:oauth:grant-type:device_code" {
				t.Error("incorrect OAuth device grant")
			}
			if pending {
				writeJSON(w, 400, map[string]any{"error": "authorization_pending"})
				return
			}
			writeJSON(w, 200, map[string]any{"access_token": "synthetic-access-token", "token_type": "Bearer", "expires_in": 900, "scope": "urn:ietf:params:oauth:scope:mail", "refresh_token": "must-not-be-stored"})
		case "/jmap/session":
			caps := map[string]any{"urn:ietf:params:jmap:mail": map[string]any{}}
			if management {
				caps["urn:stalwart:jmap"] = map[string]any{}
			}
			writeJSON(w, 200, map[string]any{"capabilities": caps, "accounts": map[string]any{"account-1": map[string]any{"isPersonal": true}}})
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(404)
		}
	}))
	defer provider.Close()
	origin = provider.URL
	oldTransport := http.DefaultTransport
	http.DefaultTransport = provider.Client().Transport
	t.Cleanup(func() { http.DefaultTransport = oldTransport })
	t.Setenv("COLLABORATION_ENABLED", "true")
	t.Setenv("STALWART_URL", origin)
	t.Setenv("STALWART_CLIENT_ID", "re8ch-self-service")
	secrets := map[string]map[string]any{}
	oldBroker := collaborationBrokerRequest
	collaborationBrokerRequest = func(_ context.Context, method, path string, payload any) (map[string]any, error) {
		switch method {
		case http.MethodPost, http.MethodPut:
			body := object(payload)
			meta := object(body["metadata"])
			name := asString(meta, "name")
			data := map[string]any{}
			for key, value := range body["stringData"].(map[string]string) {
				data[key] = base64.StdEncoding.EncodeToString([]byte(value))
			}
			metaJSON, _ := json.Marshal(meta)
			var wireMeta map[string]any
			json.Unmarshal(metaJSON, &wireMeta)
			secret := map[string]any{"metadata": wireMeta, "data": data}
			secrets[name] = secret
			return secret, nil
		case http.MethodGet:
			name := path[strings.LastIndex(path, "/")+1:]
			secret := secrets[name]
			if secret == nil {
				return nil, errors.New("not found")
			}
			return secret, nil
		default:
			return nil, errors.New("unexpected broker request")
		}
	}
	t.Cleanup(func() { collaborationBrokerRequest = oldBroker })
	plan, _ := collaborationPlan(id, []string{"mail.read"})
	started, err := s.startCollaboration(ctx, id, []string{"mail.read"}, plan["planHash"].(string), "start-key-1")
	if err != nil {
		t.Fatal(err)
	}
	ref := started["connectionRef"].(string)
	wire, _ := json.Marshal(started)
	if strings.Contains(string(wire), "synthetic-device-code") {
		t.Fatal("device credential exposed")
	}
	if _, err = s.startCollaboration(ctx, id, []string{"mail.read"}, plan["planHash"].(string), "start-key-1"); err != nil || deviceCalls != 1 {
		t.Fatal("start retried provider request")
	}
	if _, err = s.completeCollaboration(ctx, id, ref); err == nil || tokenCalls != 0 {
		t.Fatal("poll pacing bypassed")
	}
	s.db.Exec(`UPDATE collaboration_connections SET next_poll=0 WHERE ref=?`, ref)
	value, err := s.completeCollaboration(ctx, id, ref)
	if err != nil || value["state"] != "AuthorizationRequired" {
		t.Fatalf("pending failed %v %v", value, err)
	}
	pending = false
	management = true
	s.db.Exec(`UPDATE collaboration_connections SET next_poll=0 WHERE ref=?`, ref)
	if _, err = s.completeCollaboration(ctx, id, ref); err == nil {
		t.Fatal("management token accepted")
	}
	cFailed, _ := s.collaborationConnection(ctx, id, ref)
	if cFailed.State != "ReauthorizationRequired" {
		t.Fatalf("consumed device code remained retryable: %s", cFailed.State)
	}
	beforeCalls := tokenCalls
	s.completeCollaboration(ctx, id, ref)
	if tokenCalls != beforeCalls {
		t.Fatal("replayed consumed device code")
	}
	management = false
	restarted, restartErr := s.startCollaboration(ctx, id, []string{"mail.read"}, plan["planHash"].(string), "start-key-2")
	if restartErr != nil {
		t.Fatal(restartErr)
	}
	ref = restarted["connectionRef"].(string)
	s.db.Exec(`UPDATE collaboration_connections SET next_poll=0 WHERE ref=?`, ref)
	value, err = s.completeCollaboration(ctx, id, ref)
	if err != nil || value["state"] != "Ready" {
		t.Fatalf("authorization failed %v %v", value, err)
	}
	c, _ := s.collaborationConnection(ctx, id, ref)
	_, material, err := collaborationSecret(ctx, id, c)
	if err != nil {
		t.Fatal(err)
	}
	if material["accessToken"] != "synthetic-access-token" || material["deviceCode"] != "" || material["refresh_token"] != "" {
		t.Fatal("credential lifecycle incorrect")
	}
	for _, secret := range secrets {
		raw, _ := json.Marshal(secret)
		if strings.Contains(string(raw), base64.StdEncoding.EncodeToString([]byte("must-not-be-stored"))) {
			t.Fatal("retained refresh token")
		}
	}
}

func TestCollaborationCapabilitiesAreDisabledByDefault(t *testing.T) {
	t.Setenv("COLLABORATION_ENABLED", "")
	id := identity{Tenant: "re8ch", Subject: "owner", Role: "operator"}
	state := selfServiceCapabilitiesFromState(id, map[string]any{}, nil)
	for _, capability := range state["capabilities"].([]map[string]any) {
		if capability["id"] == "collaboration" {
			t.Fatal("prematurely published")
		}
	}
	t.Setenv("COLLABORATION_ENABLED", "true")
	state = selfServiceCapabilitiesFromState(id, map[string]any{}, nil)
	last := state["capabilities"].([]map[string]any)
	capability := last[len(last)-1]
	if capability["id"] != "collaboration" || capability["canConnect"] != false || capability["providerState"] != "unverified" {
		t.Fatalf("unverified capability misrepresented: %v", capability)
	}
}

func TestCollaborationRepeatRevocationAfterCredentialRemoval(t *testing.T) {
	s := testServer(t)
	ctx := context.Background()
	id := identity{Tenant: "re8ch", Subject: "owner"}
	ref := "collaboration://abcdefabcdefabcdefabcdefabcdefab"
	s.db.Exec(`INSERT INTO collaboration_connections(ref,tenant,subject,request_hash,scopes,state) VALUES(?,?,?,?,?,'Revoked')`, ref, id.Tenant, id.Subject, "hash", `["files.read"]`)
	old := collaborationBrokerRequest
	collaborationBrokerRequest = func(context.Context, string, string, any) (map[string]any, error) {
		return nil, kubeResponseError{Status: 404}
	}
	t.Cleanup(func() { collaborationBrokerRequest = old })
	for i := 0; i < 2; i++ {
		value, err := s.revokeCollaboration(ctx, id, ref)
		if err != nil || value["credentialCleanup"] != "removed" || value["state"] != "Revoked" {
			t.Fatalf("repeat revoke %v %v", value, err)
		}
	}
}

func TestCollaborationPublicToolPublication(t *testing.T) {
	for _, enabled := range []string{"false", "true"} {
		t.Setenv("COLLABORATION_ENABLED", enabled)
		ctx := context.Background()
		s := testServer(t)
		left, right := mcp.NewInMemoryTransports()
		ss, err := s.publicSelfServiceMCP(identity{Tenant: "re8ch", Subject: "owner", Role: "operator"}).Connect(ctx, left, nil)
		if err != nil {
			t.Fatal(err)
		}
		client := mcp.NewClient(&mcp.Implementation{Name: "test", Version: "1"}, nil)
		session, err := client.Connect(ctx, right, nil)
		if err != nil {
			t.Fatal(err)
		}
		result, err := session.ListTools(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		names := map[string]*mcp.Tool{}
		for _, tool := range result.Tools {
			names[tool.Name] = tool
		}
		for _, name := range []string{"collaboration_connection", "mail_query", "mail_read", "calendar_query", "calendar_respond", "contacts_query", "files_list", "files_read"} {
			if (names[name] != nil) != (enabled == "true") {
				t.Fatalf("incorrect publication of %s with enabled=%s", name, enabled)
			}
		}
		if enabled == "true" && (names["calendar_respond"].Annotations.ReadOnlyHint || !names["mail_read"].Annotations.ReadOnlyHint) {
			t.Fatal("incorrect data tool side-effect annotations")
		}
		if names["platform_tool_call"] != nil {
			t.Fatal("administrator tool escaped into public data surface")
		}
		session.Close()
		ss.Close()
	}
}

func TestCollaborationCredentialNamespaceIsolation(t *testing.T) {
	ref := "collaboration://0123456789abcdef0123456789abcdef"
	for _, tc := range []struct{ name, collaboration, shared, want string }{
		{"default", "", "", "credential-broker"},
		{"legacy override", "", "legacy-broker", "legacy-broker"},
		{"isolated", "stalwart-credentials", "credential-broker", "stalwart-credentials"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("COLLABORATION_CREDENTIAL_NAMESPACE", tc.collaboration)
			t.Setenv("CREDENTIAL_NAMESPACE", tc.shared)
			got, err := collaborationSecretPath(ref)
			want := "/api/v1/namespaces/" + tc.want + "/secrets/stalwart-0123456789abcdef0123456789abcdef"
			if err != nil || got != want {
				t.Fatalf("got %q, %v; want %q", got, err, want)
			}
		})
	}
}
