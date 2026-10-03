package main

import (
	"context"
	"encoding/json"
	"errors"
	"sort"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func (s *server) registerBYOCTools(ms *mcp.Server) {
	definitions, err := legacyDefinitions()
	if err != nil {
		panic(err)
	}
	for _, name := range []string{"byoc_cluster_register", "byoc_registration_list", "byoc_connection_list", "byoc_connection_revoke"} {
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
				value, err := s.callBYOC(id, name, args)
				return nil, value, err
			})
	}
}

func asString(args map[string]any, key string) string { value, _ := args[key].(string); return value }

func stamp() string { return time.Now().UTC().Format("2006-01-02T15:04:05.000000Z") }

func newRef() string { return strings.ReplaceAll(uuid.NewString(), "-", "") }

func (s *server) callBYOC(id identity, name string, args map[string]any) (any, error) {
	if s.ledger == nil {
		return nil, errors.New("tenant state store unavailable")
	}
	tenant := id.Tenant
	switch name {
	case "byoc_cluster_register":
		cluster, region, issuer, key := asString(args, "cluster_uid"), asString(args, "region_ref"), asString(args, "issuer"), asString(args, "public_key")
		if strings.TrimSpace(cluster) == "" || strings.TrimSpace(region) == "" || !strings.HasPrefix(issuer, "https://") || strings.TrimSpace(key) == "" {
			return nil, errors.New("clusterUid, regionRef, HTTPS issuer and publicKey are required")
		}
		capabilities := []string{}
		seen := map[string]bool{}
		if values, ok := args["capabilities"].([]any); ok {
			for _, raw := range values {
				if value, ok := raw.(string); ok && !seen[value] {
					capabilities = append(capabilities, value)
					seen[value] = true
				}
			}
		}
		sort.Strings(capabilities)
		encoded, _ := json.Marshal(capabilities)
		keyHash, err := canonicalHash(key)
		if err != nil {
			return nil, err
		}
		registration := "cluster-registration://" + tenant + "/" + newRef()
		enrollment := "enrollment://tenant/" + tenant + "/" + newRef()
		now := stamp()
		_, err = s.ledger.Exec(`INSERT INTO cluster_registrations VALUES(?,?,?,?,?,?,?,?,?,?,?)`, registration, tenant, cluster, region, issuer, keyHash, string(encoded), "PendingApproval", enrollment, now, now)
		if err != nil {
			return nil, errors.New("cluster is already registered for tenant")
		}
		return map[string]any{"registrationRef": registration, "tenantRef": tenant, "clusterUid": cluster, "regionRef": region, "state": "PendingApproval", "enrollmentRef": enrollment, "credentialMaterialReturned": false}, nil
	case "byoc_registration_list":
		rows, err := s.ledger.Query(`SELECT registration_ref,tenant_ref,cluster_uid,region_ref,issuer,public_key_hash,capabilities_json,state,created_at,updated_at FROM cluster_registrations WHERE tenant_ref=? ORDER BY created_at`, tenant)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		items := []map[string]any{}
		for rows.Next() {
			var ref, tenantRef, cluster, region, issuer, keyHash, capabilitiesJSON, state, created, updated string
			if err = rows.Scan(&ref, &tenantRef, &cluster, &region, &issuer, &keyHash, &capabilitiesJSON, &state, &created, &updated); err != nil {
				return nil, err
			}
			var caps []string
			if err = json.Unmarshal([]byte(capabilitiesJSON), &caps); err != nil {
				return nil, err
			}
			items = append(items, map[string]any{"registration_ref": ref, "tenant_ref": tenantRef, "cluster_uid": cluster, "region_ref": region, "issuer": issuer, "public_key_hash": keyHash, "capabilities": caps, "state": state, "created_at": created, "updated_at": updated})
		}
		return map[string]any{"tenantRef": tenant, "registrations": items}, rows.Err()
	case "byoc_connection_list":
		rows, err := s.ledger.Query(`SELECT connection_ref,registration_ref,tenant_ref,state,identity_ref,last_heartbeat,revoked_at,created_at FROM cluster_connections WHERE tenant_ref=? ORDER BY created_at`, tenant)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		items := []map[string]any{}
		for rows.Next() {
			var ref, registration, tenantRef, state, identityRef, created string
			var heartbeat, revoked any
			if err = rows.Scan(&ref, &registration, &tenantRef, &state, &identityRef, &heartbeat, &revoked, &created); err != nil {
				return nil, err
			}
			items = append(items, map[string]any{"connection_ref": ref, "registration_ref": registration, "tenant_ref": tenantRef, "state": state, "identity_ref": identityRef, "last_heartbeat": heartbeat, "revoked_at": revoked, "created_at": created})
		}
		return map[string]any{"tenantRef": tenant, "connections": items}, rows.Err()
	case "byoc_connection_revoke":
		state := "Revoked"
		if args["offline"] == true {
			state = "RevocationPending"
		}
		now := stamp()
		ref := asString(args, "connection_ref")
		result, err := s.ledger.Exec(`UPDATE cluster_connections SET state=?,revoked_at=? WHERE connection_ref=? AND tenant_ref=? AND state='Ready'`, state, now, ref, tenant)
		if err != nil {
			return nil, err
		}
		n, _ := result.RowsAffected()
		if n != 1 {
			return nil, errors.New("ready cluster connection not found")
		}
		return map[string]any{"connectionRef": ref, "tenantRef": tenant, "state": state, "revokedAt": now}, nil
	}
	return nil, errors.New("tool is not published")
}
