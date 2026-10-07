package main

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"errors"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// Agent messages live in the tenant state database. No model key or sandbox
// credential crosses the MCP boundary; a worker runs under its own tenant login.
func migrateAgentA2A(db *sql.DB) error {
	_, err := db.Exec(`CREATE TABLE IF NOT EXISTS a2a_agents (
		ref TEXT PRIMARY KEY, tenant TEXT NOT NULL, owner_subject TEXT NOT NULL,
		name TEXT NOT NULL, capabilities TEXT NOT NULL, lease_until INTEGER NOT NULL);
		CREATE INDEX IF NOT EXISTS a2a_agents_visible ON a2a_agents(tenant,lease_until);
		CREATE TABLE IF NOT EXISTS a2a_tasks (
		ref TEXT PRIMARY KEY, tenant TEXT NOT NULL, agent_ref TEXT NOT NULL,
		requester_subject TEXT NOT NULL, idempotency_key TEXT NOT NULL,
		message TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','received','completed')),
		reply TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
		UNIQUE(tenant,requester_subject,idempotency_key));
		CREATE INDEX IF NOT EXISTS a2a_tasks_inbox ON a2a_tasks(tenant,agent_ref,status,created_at);`)
	if err != nil {
		return err
	}
	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS a2a_receipts (
		task_ref TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
		secretary_accepted_at INTEGER, PRIMARY KEY(task_ref,status));`)
	return err
}

func agentRef(prefix string) (string, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return prefix + hex.EncodeToString(raw), nil
}

type agentRegisterInput struct {
	Name         string `json:"name" jsonschema:"Short name visible to this tenant"`
	Capabilities string `json:"capabilities" jsonschema:"Brief description of tasks this agent accepts"`
	LeaseSeconds int    `json:"leaseSeconds" jsonschema:"Availability lease, from 60 to 3600 seconds"`
}
type agentHeartbeatInput struct {
	AgentRef     string `json:"agentRef" jsonschema:"Agent reference returned by agent_register"`
	LeaseSeconds int    `json:"leaseSeconds" jsonschema:"Availability lease, from 60 to 3600 seconds"`
}
type agentSendInput struct {
	AgentRef       string `json:"agentRef" jsonschema:"Running agent reference from agent_list"`
	Message        string `json:"message" jsonschema:"Bounded task text for the receiving agent"`
	IdempotencyKey string `json:"idempotencyKey" jsonschema:"Stable unique key for this task request"`
}
type agentPollInput struct {
	AgentRef string `json:"agentRef" jsonschema:"Agent reference owned by this login"`
}
type agentTaskInput struct {
	TaskRef string `json:"taskRef" jsonschema:"Task reference returned by agent_task_send"`
}
type agentReplyInput struct {
	TaskRef string `json:"taskRef" jsonschema:"Received task reference"`
	Reply   string `json:"reply" jsonschema:"Agent's actual response to the task"`
}

func checkedLease(seconds int) (int64, error) {
	if seconds < 60 || seconds > 3600 {
		return 0, errors.New("leaseSeconds must be between 60 and 3600")
	}
	return time.Now().Unix() + int64(seconds), nil
}

func (s *server) registerAgentA2ATools(ms *mcp.Server) {
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("agent_register"), Name: "agent_register", Description: "Register a tenant agent that is running and polling for tasks. Its availability expires unless heartbeats continue."},
		func(ctx context.Context, _ *mcp.CallToolRequest, in agentRegisterInput) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			if id.Role != "operator" && id.Role != "admin" {
				return nil, nil, errors.New("an operator tenant role is required")
			}
			name, capabilities := strings.TrimSpace(in.Name), strings.TrimSpace(in.Capabilities)
			if len(name) < 1 || len(name) > 80 || len(capabilities) > 500 {
				return nil, nil, errors.New("invalid name or capabilities")
			}
			until, err := checkedLease(in.LeaseSeconds)
			if err != nil {
				return nil, nil, err
			}
			ref, err := agentRef("agent_")
			if err != nil {
				return nil, nil, err
			}
			_, err = s.db.ExecContext(ctx, `INSERT INTO a2a_agents VALUES(?,?,?,?,?,?)`, ref, id.Tenant, id.Subject, name, capabilities, until)
			return nil, map[string]any{"agentRef": ref, "leaseUntil": until}, err
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("agent_heartbeat"), Name: "agent_heartbeat", Description: "Renew the running agent's availability lease."},
		func(ctx context.Context, _ *mcp.CallToolRequest, in agentHeartbeatInput) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			until, err := checkedLease(in.LeaseSeconds)
			if err != nil {
				return nil, nil, err
			}
			result, err := s.db.ExecContext(ctx, `UPDATE a2a_agents SET lease_until=? WHERE ref=? AND tenant=? AND owner_subject=?`, until, in.AgentRef, id.Tenant, id.Subject)
			if err != nil {
				return nil, nil, err
			}
			n, _ := result.RowsAffected()
			if n != 1 {
				return nil, nil, errors.New("agent not found")
			}
			return nil, map[string]any{"agentRef": in.AgentRef, "leaseUntil": until}, nil
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("agent_list"), Name: "agent_list", Description: "List running agents visible to the authenticated tenant."},
		func(ctx context.Context, _ *mcp.CallToolRequest, _ struct{}) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			rows, err := s.db.QueryContext(ctx, `SELECT ref,name,capabilities,lease_until FROM a2a_agents WHERE tenant=? AND lease_until>? ORDER BY name,ref LIMIT 100`, id.Tenant, time.Now().Unix())
			if err != nil {
				return nil, nil, err
			}
			defer rows.Close()
			agents := []map[string]any{}
			for rows.Next() {
				var ref, name, cap string
				var until int64
				if err = rows.Scan(&ref, &name, &cap, &until); err != nil {
					return nil, nil, err
				}
				agents = append(agents, map[string]any{"agentRef": ref, "name": name, "capabilities": cap, "leaseUntil": until})
			}
			return nil, map[string]any{"agents": agents}, rows.Err()
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("agent_task_send"), Name: "agent_task_send", Description: "Queue one task for a running tenant agent; return a resumable task reference."},
		func(ctx context.Context, _ *mcp.CallToolRequest, in agentSendInput) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			if len(strings.TrimSpace(in.Message)) < 1 || len(in.Message) > 16384 || len(in.IdempotencyKey) < 8 || len(in.IdempotencyKey) > 128 {
				return nil, nil, errors.New("invalid message or idempotencyKey")
			}
			var previousRef, previousAgent, previousMessage, previousStatus string
			err = s.db.QueryRowContext(ctx, `SELECT ref,agent_ref,message,status FROM a2a_tasks WHERE tenant=? AND requester_subject=? AND idempotency_key=?`, id.Tenant, id.Subject, in.IdempotencyKey).Scan(&previousRef, &previousAgent, &previousMessage, &previousStatus)
			if err == nil {
				if previousAgent != in.AgentRef || previousMessage != in.Message {
					return nil, nil, errors.New("idempotencyKey already used for a different task")
				}
				if err = recordA2AReceipt(ctx, s.db, previousRef, id.Tenant, id.Subject, in.AgentRef, "queued", in.Message); err != nil {
					return nil, nil, err
				}
				return nil, map[string]any{"taskRef": previousRef, "status": previousStatus, "secretaryReceipt": flushA2AReceipt(ctx, s.db, previousRef, "queued")}, nil
			}
			if !errors.Is(err, sql.ErrNoRows) {
				return nil, nil, err
			}
			var live int
			err = s.db.QueryRowContext(ctx, `SELECT 1 FROM a2a_agents WHERE ref=? AND tenant=? AND lease_until>?`, in.AgentRef, id.Tenant, time.Now().Unix()).Scan(&live)
			if err != nil {
				return nil, nil, errors.New("running agent not found")
			}
			ref, err := agentRef("task_")
			if err != nil {
				return nil, nil, err
			}
			now := time.Now().Unix()
			_, err = s.db.ExecContext(ctx, `INSERT INTO a2a_tasks(ref,tenant,agent_ref,requester_subject,idempotency_key,message,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?) ON CONFLICT(tenant,requester_subject,idempotency_key) DO NOTHING`, ref, id.Tenant, in.AgentRef, id.Subject, in.IdempotencyKey, in.Message, now, now)
			if err != nil {
				return nil, nil, err
			}
			var actual, agent, message, status string
			err = s.db.QueryRowContext(ctx, `SELECT ref,agent_ref,message,status FROM a2a_tasks WHERE tenant=? AND requester_subject=? AND idempotency_key=?`, id.Tenant, id.Subject, in.IdempotencyKey).Scan(&actual, &agent, &message, &status)
			if err != nil {
				return nil, nil, err
			}
			if agent != in.AgentRef || message != in.Message {
				return nil, nil, errors.New("idempotencyKey already used for a different task")
			}
			if err = recordA2AReceipt(ctx, s.db, actual, id.Tenant, id.Subject, in.AgentRef, "queued", in.Message); err != nil {
				return nil, nil, err
			}
			return nil, map[string]any{"taskRef": actual, "status": status, "secretaryReceipt": flushA2AReceipt(ctx, s.db, actual, "queued")}, nil
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("agent_task_poll"), Name: "agent_task_poll", Description: "Receive the oldest queued task for an agent owned by this login. Marks it received."},
		func(ctx context.Context, _ *mcp.CallToolRequest, in agentPollInput) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			tx, err := s.db.BeginTx(ctx, nil)
			if err != nil {
				return nil, nil, err
			}
			defer tx.Rollback()
			var live int
			err = tx.QueryRowContext(ctx, `SELECT 1 FROM a2a_agents WHERE ref=? AND tenant=? AND owner_subject=? AND lease_until>?`, in.AgentRef, id.Tenant, id.Subject, time.Now().Unix()).Scan(&live)
			if err != nil {
				return nil, nil, errors.New("running agent not found or not owned by caller")
			}
			var ref, message string
			err = tx.QueryRowContext(ctx, `SELECT ref,message FROM a2a_tasks WHERE tenant=? AND agent_ref=? AND (status='queued' OR (status='received' AND updated_at<?)) ORDER BY created_at,ref LIMIT 1`, id.Tenant, in.AgentRef, time.Now().Unix()-60).Scan(&ref, &message)
			if errors.Is(err, sql.ErrNoRows) {
				return nil, map[string]any{"task": nil}, nil
			}
			if err != nil {
				return nil, nil, err
			}
			if _, err = tx.ExecContext(ctx, `UPDATE a2a_tasks SET status='received',updated_at=? WHERE ref=? AND status IN ('queued','received')`, time.Now().Unix(), ref); err != nil {
				return nil, nil, err
			}
			if err = tx.Commit(); err != nil {
				return nil, nil, err
			}
			if err = recordA2AReceipt(ctx, s.db, ref, id.Tenant, in.AgentRef, ref, "received", message); err != nil {
				return nil, nil, err
			}
			return nil, map[string]any{"task": map[string]any{"taskRef": ref, "message": message, "status": "received"}, "secretaryReceipt": flushA2AReceipt(ctx, s.db, ref, "received")}, nil
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("agent_task_reply"), Name: "agent_task_reply", Description: "Complete a received task with the agent's actual reply."},
		func(ctx context.Context, _ *mcp.CallToolRequest, in agentReplyInput) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			if len(in.Reply) < 1 || len(in.Reply) > 32768 {
				return nil, nil, errors.New("invalid reply")
			}
			result, err := s.db.ExecContext(ctx, `UPDATE a2a_tasks SET status='completed',reply=?,updated_at=? WHERE ref=? AND tenant=? AND status='received' AND agent_ref IN (SELECT ref FROM a2a_agents WHERE owner_subject=? AND tenant=?)`, in.Reply, time.Now().Unix(), in.TaskRef, id.Tenant, id.Subject, id.Tenant)
			if err != nil {
				return nil, nil, err
			}
			n, _ := result.RowsAffected()
			if n != 1 {
				var status, previousReply string
				err = s.db.QueryRowContext(ctx, `SELECT status,reply FROM a2a_tasks WHERE ref=? AND tenant=? AND agent_ref IN (SELECT ref FROM a2a_agents WHERE owner_subject=? AND tenant=?)`, in.TaskRef, id.Tenant, id.Subject, id.Tenant).Scan(&status, &previousReply)
				if err == nil && status == "completed" && previousReply == in.Reply {
					if err = recordA2AReceipt(ctx, s.db, in.TaskRef, id.Tenant, id.Subject, in.TaskRef, "completed", in.Reply); err != nil {
						return nil, nil, err
					}
					return nil, map[string]any{"taskRef": in.TaskRef, "status": "completed", "secretaryReceipt": flushA2AReceipt(ctx, s.db, in.TaskRef, "completed")}, nil
				}
				return nil, nil, errors.New("received task not found or caller does not own agent")
			}
			if err = recordA2AReceipt(ctx, s.db, in.TaskRef, id.Tenant, id.Subject, in.TaskRef, "completed", in.Reply); err != nil {
				return nil, nil, err
			}
			return nil, map[string]any{"taskRef": in.TaskRef, "status": "completed", "secretaryReceipt": flushA2AReceipt(ctx, s.db, in.TaskRef, "completed")}, nil
		})
	mcp.AddTool(ms, &mcp.Tool{Annotations: toolAnnotations("agent_task_get"), Name: "agent_task_get", Description: "Read task status and actual reply for its original requester."},
		func(ctx context.Context, _ *mcp.CallToolRequest, in agentTaskInput) (*mcp.CallToolResult, any, error) {
			id, err := ownerScopedIdentityFrom(ctx)
			if err != nil {
				return nil, nil, err
			}
			var agent, status, reply string
			var updated int64
			err = s.db.QueryRowContext(ctx, `SELECT agent_ref,status,reply,updated_at FROM a2a_tasks WHERE ref=? AND tenant=? AND requester_subject=?`, in.TaskRef, id.Tenant, id.Subject).Scan(&agent, &status, &reply, &updated)
			if err != nil {
				return nil, nil, errors.New("task not found")
			}
			return nil, map[string]any{"taskRef": in.TaskRef, "agentRef": agent, "status": status, "reply": reply, "updatedAt": updated, "secretaryReceipts": flushA2AReceipts(ctx, s.db, in.TaskRef)}, nil
		})
}
