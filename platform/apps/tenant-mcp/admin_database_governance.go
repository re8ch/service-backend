package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"regexp"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const adminDatabaseOrganization = "admin"

var adminDatabaseOperationKey = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9:/._#-]{7,127}$`)

func adminDatabaseInventory() (map[string]any, string, error) {
	inventory := map[string]any{
		"module":                     "admin.database-governance.v1",
		"organizationRef":            adminDatabaseOrganization,
		"databases":                  []string{"re8ch_billing", "re8ch_identity"},
		"operations":                 []string{"inventory", "read", "plan", "apply"},
		"writeGuards":                []string{"approvalId", "inventoryHash", "planHash", "idempotencyKey"},
		"credentialRef":              "credential://platform/admin/system-postgresql",
		"credentialMaterialReturned": false,
		"staticClusterChanges":       "gitops-cloud-ssa",
	}
	hash, err := canonicalHash(inventory)
	return inventory, hash, err
}

func adminDatabaseIdentity(ctx context.Context) (identity, error) {
	id, err := platformIdentity(ctx)
	if err != nil {
		return identity{}, err
	}
	if id.Tenant != adminDatabaseOrganization {
		return identity{}, errors.New("Admin database governance requires the active admin organization")
	}
	return id, nil
}

func validateAdminDatabaseInput(input map[string]any) error {
	if err := validatePlatformPostgresInput(input); err != nil {
		return err
	}
	readOnly, present := input["readOnly"].(bool)
	if !present {
		readOnly = true
	}
	if readOnly {
		return nil
	}
	_, inventoryHash, err := adminDatabaseInventory()
	if err != nil {
		return err
	}
	if input["inventoryHash"] != inventoryHash {
		return errors.New("writes require the exact current Admin database governance inventoryHash")
	}
	key, _ := input["idempotencyKey"].(string)
	if !adminDatabaseOperationKey.MatchString(key) {
		return errors.New("writes require an 8 to 128 character idempotencyKey")
	}
	return nil
}

func (s *server) callAdminDatabaseSQL(ctx context.Context, input map[string]any) (*mcp.CallToolResult, error) {
	if _, err := adminDatabaseIdentity(ctx); err != nil {
		return nil, err
	}
	if err := validateAdminDatabaseInput(input); err != nil {
		return nil, err
	}
	readOnly, present := input["readOnly"].(bool)
	if !present {
		readOnly = true
	}
	apply, _ := input["apply"].(bool)
	if readOnly || !apply {
		return s.callAdminDatabaseBackend(ctx, input)
	}
	if s.ledger == nil {
		return nil, errors.New("Admin database idempotency store unavailable")
	}
	requestHash, err := canonicalHash(input)
	if err != nil {
		return nil, err
	}
	key := input["idempotencyKey"].(string)
	var storedHash, state string
	var storedResult sql.NullString
	err = s.ledger.QueryRow(`SELECT request_hash,state,result_json FROM admin_database_operations
		WHERE organization_ref=? AND idempotency_key=?`, adminDatabaseOrganization, key).Scan(&storedHash, &state, &storedResult)
	if err == nil {
		if storedHash != requestHash {
			return nil, errors.New("idempotencyKey was already used for a different Admin database operation")
		}
		if state != "completed" || !storedResult.Valid {
			return nil, errors.New("Admin database operation outcome is pending; inspect audit evidence before choosing a new key")
		}
		var replay mcp.CallToolResult
		if err := json.Unmarshal([]byte(storedResult.String), &replay); err != nil {
			return nil, errors.New("stored Admin database operation result is invalid")
		}
		if replay.Meta == nil {
			replay.Meta = mcp.Meta{}
		}
		replay.Meta["re8ch.com/idempotent-replay"] = true
		return &replay, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	now := stamp()
	if _, err = s.ledger.Exec(`INSERT INTO admin_database_operations VALUES(?,?,?,?,?,?,?)`,
		adminDatabaseOrganization, key, requestHash, "pending", nil, now, now); err != nil {
		return nil, err
	}
	result, err := s.callAdminDatabaseBackend(ctx, input)
	if err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		return nil, err
	}
	if _, err = s.ledger.Exec(`UPDATE admin_database_operations SET state='completed',result_json=?,updated_at=?
		WHERE organization_ref=? AND idempotency_key=? AND request_hash=?`, string(encoded), stamp(),
		adminDatabaseOrganization, key, requestHash); err != nil {
		return nil, err
	}
	return result, nil
}

func (s *server) callAdminDatabaseBackend(ctx context.Context, input map[string]any) (*mcp.CallToolResult, error) {
	value, err := s.moduleRegistry().call(ctx, "tenant_admin", func(session *mcp.ClientSession) (any, error) {
		return session.CallTool(ctx, &mcp.CallToolParams{Name: "postgres_admin_sql", Arguments: input})
	})
	if err != nil {
		return nil, err
	}
	result, ok := value.(*mcp.CallToolResult)
	if !ok {
		return nil, errors.New("Admin database backend returned an invalid result")
	}
	return result, nil
}
