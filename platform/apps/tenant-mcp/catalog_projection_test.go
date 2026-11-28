package main

import (
	"context"
	"database/sql"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestCatalogProjectionPublishesSchemaAndNotifiesWithdrawal(t *testing.T) {
	s := catalogFixture(t)
	endpoint := "http://orders.platform.svc/mcp"
	s.modules = &platformRegistry{entries: map[string]*platformModule{"orders": {backend: platformBackend{URL: endpoint, AuthMode: "none"}}}}
	s.modulesOnce.Do(func() {})
	definition := &mcpCatalogDefinition{
		ServiceName: "orders", Class: "integration.mcp.byoc.v1", Endpoint: endpoint,
		AuthMode: "binding-reference", AudienceOrgRefs: []string{"acme"}, Meters: map[string]string{"requests": "request"},
		Tools: []mcpCatalogTool{{Name: "orders_search", Description: "Search orders", InputSchema: map[string]any{"type": "object", "required": []any{"query"}, "properties": map[string]any{"query": map[string]any{"type": "string"}}}}},
	}
	applied, err := s.applyMCPCatalog(context.Background(), mcpCatalogCommand{Action: "apply", TenantRef: "acme", OperationKey: "register", Definition: definition}, "operator")
	if err != nil {
		t.Fatal(err)
	}
	ms := mcp.NewServer(&mcp.Implementation{Name: "catalog-projection-test", Version: "1"}, &mcp.ServerOptions{Capabilities: &mcp.ServerCapabilities{Tools: &mcp.ToolCapabilities{ListChanged: true}}})
	id := identity{Tenant: "acme", Subject: "user"}
	names, err := s.registerCatalogProjection(ms, id)
	if err != nil || len(names) != 1 || names[0] != "orders_search" {
		t.Fatalf("active revision was not projected: %v", names)
	}
	s.catalogServers = &activeMCPServers{entries: map[string]*activeMCPServer{"acme": {server: ms, identity: id, dynamic: names, requests: 1}}}
	left, right := mcp.NewInMemoryTransports()
	serverSession, err := ms.Connect(context.Background(), left, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer serverSession.Close()
	changed := make(chan struct{}, 1)
	client := mcp.NewClient(&mcp.Implementation{Name: "catalog-client", Version: "1"}, &mcp.ClientOptions{ToolListChangedHandler: func(context.Context, *mcp.ToolListChangedRequest) { changed <- struct{}{} }})
	clientSession, err := client.Connect(context.Background(), right, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer clientSession.Close()
	listed, err := clientSession.ListTools(context.Background(), nil)
	if err != nil || len(listed.Tools) != 1 {
		t.Fatalf("projected tool is missing: %#v %v", listed, err)
	}
	schema, _ := listed.Tools[0].InputSchema.(map[string]any)
	if listed.Tools[0].Name != "orders_search" || schema["type"] != "object" {
		t.Fatalf("projected schema is wrong: %#v %v", listed, err)
	}
	unavailable, err := sql.Open("sqlite", t.TempDir()+"/unavailable.db")
	if err != nil {
		t.Fatal(err)
	}
	unavailable.Close()
	s.catalogAuthority = unavailable
	s.catalogServers.refreshCatalog(s, "acme")
	listed, err = clientSession.ListTools(context.Background(), nil)
	if err != nil || len(listed.Tools) != 1 || listed.Tools[0].Name != "orders_search" {
		t.Fatalf("last valid projection was not retained: %#v %v", listed, err)
	}
	s.catalogAuthority = nil
	_, err = s.applyMCPCatalog(context.Background(), mcpCatalogCommand{Action: "withdraw", TenantRef: "acme", OperationKey: "withdraw", Revision: applied["revision"].(string)}, "operator")
	if err != nil {
		t.Fatal(err)
	}
	select {
	case <-changed:
	case <-time.After(time.Second):
		t.Fatal("withdrawal did not emit tools/list_changed")
	}
	listed, err = clientSession.ListTools(context.Background(), nil)
	if err != nil || len(listed.Tools) != 0 {
		t.Fatalf("withdrawn tool remains published: %#v %v", listed, err)
	}
}
