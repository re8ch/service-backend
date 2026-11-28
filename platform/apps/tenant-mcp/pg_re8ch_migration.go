package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"regexp"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const pgRe8chProjectID = "pg-re8ch"

var pgRe8chMigrationName = regexp.MustCompile(`^[a-z][a-z0-9_]{2,63}$`)

type pgRe8chMigrationPreviewInput struct {
	Name  string `json:"name"`
	Query string `json:"query"`
}

type pgRe8chMigrationApplyInput struct {
	Name           string `json:"name"`
	Query          string `json:"query"`
	PlanHash       string `json:"planHash"`
	ApprovalID     string `json:"approvalId"`
	IdempotencyKey string `json:"idempotencyKey"`
}

func pgRe8chMigrationPlan(name, query string) (map[string]any, string, error) {
	if !pgRe8chMigrationName.MatchString(name) {
		return nil, "", errors.New("migration name must be 3 to 64 lowercase letters, digits, or underscores")
	}
	query = strings.TrimSpace(query)
	if query == "" || len(query) > 1<<20 {
		return nil, "", errors.New("migration query must contain 1 to 1048576 bytes")
	}
	digest := sha256.Sum256([]byte(query))
	plan := map[string]any{
		"projectId": pgRe8chProjectID,
		"name":      name,
		"sqlSha256": hex.EncodeToString(digest[:]),
		"tool":      "apply_migration",
	}
	planHash, err := canonicalHash(plan)
	return plan, planHash, err
}

func (s *server) pgRe8chAdminMCP() *mcp.Server {
	ms := mcp.NewServer(&mcp.Implementation{Name: "re8ch-pg-re8ch-admin", Version: "0.1.0"},
		&mcp.ServerOptions{Capabilities: &mcp.ServerCapabilities{Tools: &mcp.ToolCapabilities{}}})
	ms.AddReceivingMiddleware(verifiedMCPSessionIdentity, s.diagnostics().middleware, mcpToolTelemetry.middleware)

	mcp.AddTool(ms, &mcp.Tool{
		Name:        "pg_re8ch_migration_preview",
		Description: "Preview one Supabase migration against the fixed pg-re8ch project. Returns only the target, migration name, SQL SHA-256, and exact planHash; SQL and credentials are not persisted in Git.",
		Annotations: toolAnnotations("pg_re8ch_migration_preview"),
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input pgRe8chMigrationPreviewInput) (*mcp.CallToolResult, any, error) {
		if _, err := adminDatabaseIdentity(ctx); err != nil {
			return nil, nil, err
		}
		plan, planHash, err := pgRe8chMigrationPlan(input.Name, input.Query)
		if err != nil {
			return nil, nil, err
		}
		plan["planHash"] = planHash
		plan["decision"] = "preview"
		return nil, plan, nil
	})

	mcp.AddTool(ms, &mcp.Tool{
		Name:        "pg_re8ch_migration_apply",
		Description: "Apply the exact previewed migration through Supabase apply_migration. Requires Admin membership, the current planHash, an approvalId, and a unique idempotencyKey.",
		Annotations: toolAnnotations("pg_re8ch_migration_apply"),
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input pgRe8chMigrationApplyInput) (*mcp.CallToolResult, any, error) {
		if _, err := adminDatabaseIdentity(ctx); err != nil {
			return nil, nil, err
		}
		plan, expected, err := pgRe8chMigrationPlan(input.Name, input.Query)
		if err != nil {
			return nil, nil, err
		}
		if input.PlanHash != expected {
			return nil, nil, errors.New("apply requires the exact current planHash")
		}
		if strings.TrimSpace(input.ApprovalID) == "" {
			return nil, nil, errors.New("apply requires a non-empty approvalId")
		}
		if !adminDatabaseOperationKey.MatchString(input.IdempotencyKey) {
			return nil, nil, errors.New("apply requires an 8 to 128 character idempotencyKey")
		}
		if s.ledger == nil {
			return nil, nil, errors.New("migration idempotency store unavailable")
		}
		request := map[string]any{
			"plan":           plan,
			"planHash":       input.PlanHash,
			"approvalId":     input.ApprovalID,
			"idempotencyKey": input.IdempotencyKey,
		}
		requestHash, err := canonicalHash(request)
		if err != nil {
			return nil, nil, err
		}
		ledgerKey := "pg-re8ch-migration:" + input.IdempotencyKey
		var storedHash, state string
		var storedResult []byte
		err = s.ledger.QueryRow(`SELECT request_hash,state,result_json FROM admin_database_operations
			WHERE organization_ref=? AND idempotency_key=?`, adminDatabaseOrganization, ledgerKey).Scan(&storedHash, &state, &storedResult)
		if err == nil {
			if storedHash != requestHash {
				return nil, nil, errors.New("idempotencyKey was already used for a different migration")
			}
			if state != "completed" || len(storedResult) == 0 {
				return nil, nil, errors.New("migration outcome is pending; inspect audit evidence before retrying")
			}
			var replay mcp.CallToolResult
			if json.Unmarshal(storedResult, &replay) != nil {
				return nil, nil, errors.New("stored migration result is invalid")
			}
			if replay.Meta == nil {
				replay.Meta = mcp.Meta{}
			}
			replay.Meta["re8ch.com/idempotent-replay"] = true
			return &replay, nil, nil
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return nil, nil, err
		}
		if _, err = s.ledger.Exec(`INSERT INTO admin_database_operations VALUES(?,?,?,?,?,?,?)`,
			adminDatabaseOrganization, ledgerKey, requestHash, "pending", nil, stamp(), stamp()); err != nil {
			return nil, nil, err
		}
		value, err := s.moduleRegistry().call(ctx, "pg_re8ch", func(session *mcp.ClientSession) (any, error) {
			return session.CallTool(ctx, &mcp.CallToolParams{Name: "apply_migration", Arguments: map[string]any{
				"project_id": pgRe8chProjectID,
				"name":       input.Name,
				"query":      input.Query,
			}})
		})
		if err != nil {
			return nil, nil, err
		}
		result, ok := value.(*mcp.CallToolResult)
		if !ok {
			return nil, nil, errors.New("Supabase migration backend returned an invalid result")
		}
		encoded, err := json.Marshal(result)
		if err != nil {
			return nil, nil, err
		}
		if _, err = s.ledger.Exec(`UPDATE admin_database_operations SET state='completed',result_json=?,updated_at=?
			WHERE organization_ref=? AND idempotency_key=? AND request_hash=?`, encoded, stamp(),
			adminDatabaseOrganization, ledgerKey, requestHash); err != nil {
			return nil, nil, err
		}
		if result.Meta == nil {
			result.Meta = mcp.Meta{}
		}
		result.Meta["re8ch.com/plan-hash"] = expected
		result.Meta["re8ch.com/sql-sha256"] = plan["sqlSha256"]
		return result, nil, nil
	})
	s.registerPgRe8chReadbackTools(ms)
	return ms
}

var pgRe8chReadbackTools = map[string]struct{}{
	"execute_sql":     {},
	"list_migrations": {},
	"list_tables":     {},
}

// registerPgRe8chReadbackTools preserves the schemas published by Supabase,
// while keeping raw migration and every other write-capable operation out of
// this narrowly scoped public resource server.
func (s *server) registerPgRe8chReadbackTools(ms *mcp.Server) {
	registry := s.moduleRegistry()
	entry := registry.snapshot()["pg_re8ch"]
	if entry == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	tools, err := entry.discover(ctx, "pg_re8ch")
	cancel()
	if err != nil {
		return
	}
	for _, discovered := range tools {
		upstream := asString(discovered, "name")
		if _, ok := pgRe8chReadbackTools[upstream]; !ok {
			continue
		}
		schema, _ := discovered["inputSchema"].(map[string]any)
		upstreamName := upstream
		destructive := false
		openWorld := true
		mcp.AddTool(ms, &mcp.Tool{
			Name:        "pg_re8ch_" + upstream,
			Description: "Admin-only readback through the fixed pg-re8ch Supabase project. " + asString(discovered, "description"),
			InputSchema: schema,
			Annotations: &mcp.ToolAnnotations{
				ReadOnlyHint:    true,
				DestructiveHint: &destructive,
				OpenWorldHint:   &openWorld,
			},
		}, func(ctx context.Context, _ *mcp.CallToolRequest, input map[string]any) (*mcp.CallToolResult, any, error) {
			if _, err := adminDatabaseIdentity(ctx); err != nil {
				return nil, nil, err
			}
			if projectID := asString(input, "project_id"); projectID != "" && projectID != pgRe8chProjectID {
				return nil, nil, errors.New("readback is fixed to the pg-re8ch project")
			}
			input["project_id"] = pgRe8chProjectID
			if err := validateNativeServiceCall("pg_re8ch", upstreamName, input); err != nil {
				return nil, nil, err
			}
			value, err := registry.call(ctx, "pg_re8ch", func(session *mcp.ClientSession) (any, error) {
				return session.CallTool(ctx, &mcp.CallToolParams{Name: upstreamName, Arguments: input})
			})
			if err != nil {
				return nil, nil, err
			}
			result, ok := value.(*mcp.CallToolResult)
			if !ok {
				return nil, nil, errors.New("Supabase readback backend returned an invalid result")
			}
			return result, nil, nil
		})
	}
}
