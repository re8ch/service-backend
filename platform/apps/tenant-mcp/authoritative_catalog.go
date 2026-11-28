package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func (s *server) authoritativeCatalog(ctx context.Context, tenant string) ([]mcpCatalogEntry, error) {
	rows, err := s.catalogAuthority.QueryContext(ctx, `SELECT server_ref,owner_org_ref,slug,revision_ref,
		activation_sequence,revision_definition,tool_name,input_schema,tool_definition
		FROM bifurcate.active_mcp_catalog WHERE owner_org_ref=$1
		ORDER BY server_ref,activation_sequence,tool_name`, tenant)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	byRevision := map[string]*mcpCatalogEntry{}
	var order []string
	for rows.Next() {
		var serverRef, owner, slug, revision, toolName string
		var sequence int64
		var definitionRaw, schemaRaw, toolRaw []byte
		if err = rows.Scan(&serverRef, &owner, &slug, &revision, &sequence, &definitionRaw, &toolName, &schemaRaw, &toolRaw); err != nil {
			return nil, err
		}
		entry := byRevision[revision]
		if entry == nil {
			entry = &mcpCatalogEntry{Revision: revision, State: "active"}
			if err = json.Unmarshal(definitionRaw, &entry.Definition); err != nil {
				return nil, err
			}
			entry.Definition.ServiceName = slug
			entry.Definition.AudienceOrgRefs = []string{owner}
			byRevision[revision] = entry
			order = append(order, revision)
		}
		var schema map[string]any
		var toolDefinition struct {
			Description string               `json:"description"`
			Annotations *mcp.ToolAnnotations `json:"annotations"`
		}
		if err = json.Unmarshal(schemaRaw, &schema); err != nil {
			return nil, err
		}
		if err = json.Unmarshal(toolRaw, &toolDefinition); err != nil {
			return nil, err
		}
		entry.Definition.Tools = append(entry.Definition.Tools, mcpCatalogTool{Name: toolName, Description: toolDefinition.Description, InputSchema: schema, Annotations: toolDefinition.Annotations})
	}
	entries := make([]mcpCatalogEntry, 0, len(order))
	for _, revision := range order {
		entry := *byRevision[revision]
		if !strings.HasPrefix(entry.Definition.Class, "integration.mcp.") {
			return nil, errors.New("dynamic Catalog revision attempted to enter the protected static lane")
		}
		entries = append(entries, entry)
	}
	return entries, rows.Err()
}

func (s *server) watchCatalog(ctx context.Context) {
	if s.catalogAuthority == nil {
		return
	}
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		rows, err := s.catalogAuthority.QueryContext(ctx, `SELECT event_id,event_type,payload FROM bifurcate_api.claim_mcp_catalog_projection_events(100)`)
		if err == nil {
			var events []int64
			for rows.Next() {
				var eventID int64
				var eventType string
				var payload []byte
				if rows.Scan(&eventID, &eventType, &payload) == nil {
					events = append(events, eventID)
				}
			}
			rows.Close()
			if len(events) > 0 && (s.catalogServers == nil || s.catalogServers.refreshCatalog(s, "*")) {
				for _, eventID := range events {
					_, _ = s.catalogAuthority.ExecContext(ctx, `SELECT bifurcate_api.ack_mcp_catalog_projection_event($1)`, eventID)
				}
			}
		} else if err != nil && ctx.Err() == nil {
			log.Printf("catalog projection outbox unavailable: %v", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
