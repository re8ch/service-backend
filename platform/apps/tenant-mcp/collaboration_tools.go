package main

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type collaborationConnectionInput struct {
	Action            string   `json:"action" jsonschema:"plan, start, complete, status, list, or revoke"`
	ConnectionRef     string   `json:"connectionRef,omitempty" jsonschema:"Opaque connection reference returned by this tool"`
	Scopes            []string `json:"scopes,omitempty" jsonschema:"Explicit mail.read, calendar.read, calendar.respond, contacts.read, files.read permissions"`
	AuthorizationMode string   `json:"authorizationMode,omitempty" jsonschema:"userhub (default when published) or device"`
	PlanHash          string   `json:"planHash,omitempty" jsonschema:"Exact current plan hash for start"`
	IdempotencyKey    string   `json:"idempotencyKey,omitempty" jsonschema:"Stable unique key for start; use a new key to reconnect"`
}
type collaborationQueryInput struct {
	ConnectionRef string `json:"connectionRef"`
	Action        string `json:"action,omitempty" jsonschema:"Query action: list, search, or get; default search"`
	Text          string `json:"text,omitempty"`
	MailboxID     string `json:"mailboxId,omitempty"`
	AddressBookID string `json:"addressBookId,omitempty"`
	ContactID     string `json:"contactId,omitempty"`
	EventID       string `json:"eventId,omitempty"`
	After         string `json:"after,omitempty" jsonschema:"Inclusive RFC3339 start"`
	Before        string `json:"before,omitempty" jsonschema:"Exclusive RFC3339 end, within 31 days of start"`
	TimeZone      string `json:"timeZone,omitempty" jsonschema:"IANA time zone"`
	Position      int    `json:"position,omitempty"`
	Limit         int    `json:"limit,omitempty"`
}
type collaborationMailReadInput struct {
	ConnectionRef string `json:"connectionRef"`
	EmailID       string `json:"emailId"`
}
type collaborationFileInput struct {
	ConnectionRef string `json:"connectionRef"`
	Href          string `json:"href" jsonschema:"Provider file href from an authorized collection; no arbitrary URLs"`
}
type collaborationRespondInput struct {
	ConnectionRef  string `json:"connectionRef"`
	EventID        string `json:"eventId"`
	Response       string `json:"response" jsonschema:"accepted, declined or tentative"`
	Apply          bool   `json:"apply,omitempty" jsonschema:"False returns a plan; true records the requested response and may notify the organizer"`
	ExpectedState  string `json:"expectedState,omitempty"`
	PlanHash       string `json:"planHash,omitempty"`
	IdempotencyKey string `json:"idempotencyKey,omitempty"`
}

func collaborationCaller(ctx context.Context) (identity, error) {
	id, err := identityFrom(ctx)
	if err != nil {
		return id, err
	}
	if err = requirePersonalCollaborationGrant(ctx, id); err != nil {
		return id, err
	}
	return id, nil
}
func (s *server) callCollaborationConnection(ctx context.Context, id identity, in collaborationConnectionInput) (any, error) {
	switch in.Action {
	case "plan":
		return collaborationPlanMode(id, in.Scopes, in.AuthorizationMode)
	case "start":
		return s.startCollaborationMode(ctx, id, in.Scopes, in.PlanHash, in.IdempotencyKey, in.AuthorizationMode)
	case "complete":
		return s.completeCollaboration(ctx, id, in.ConnectionRef)
	case "status":
		c, e := s.collaborationConnection(ctx, id, in.ConnectionRef)
		if e != nil {
			return nil, e
		}
		return c.public(), nil
	case "revoke":
		return s.revokeCollaboration(ctx, id, in.ConnectionRef)
	case "list":
		rows, err := s.db.QueryContext(ctx, `SELECT `+collaborationColumns+` FROM collaboration_connections WHERE tenant=? AND subject=? ORDER BY ref LIMIT 101`, id.Tenant, id.Subject)
		if err != nil {
			return nil, err
		}
		defer rows.Close()
		items := []map[string]any{}
		for rows.Next() {
			c, e := scanCollaboration(rows)
			if e != nil {
				return nil, e
			}
			items = append(items, c.public())
		}
		truncated := len(items) > 100
		if truncated {
			items = items[:100]
		}
		return map[string]any{"connections": items, "truncated": truncated}, rows.Err()
	default:
		return nil, errors.New("unsupported collaboration connection action")
	}
}
func (s *server) registerCollaborationTools(ms *mcp.Server) {
	if !collaborationEnabled() {
		return
	}
	mcp.AddTool(ms, &mcp.Tool{Name: "collaboration_connection", Description: "Plan and authorize your existing Stalwart account through User Hub SSO by default or explicit provider device consent, then inspect or revoke your user-owned connection. Never supply passwords or tokens. Does not provision a new mailbox.", Annotations: toolAnnotations("collaboration_connection")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationConnectionInput) (*mcp.CallToolResult, any, error) {
		id, err := identityFrom(ctx)
		if err != nil {
			return nil, nil, err
		}
		// Revocation remains possible after a resource grant is removed.
		if in.Action != "revoke" && in.Action != "status" && in.Action != "list" {
			if err = requirePersonalCollaborationGrant(ctx, id); err != nil {
				return nil, nil, err
			}
		}
		value, err := s.callCollaborationConnection(ctx, id, in)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "mail_query", Description: "List your authorized mailboxes or search mail summaries with bounded pagination.", Annotations: toolAnnotations("mail_query")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationQueryInput) (*mcp.CallToolResult, any, error) {
		id, err := collaborationCaller(ctx)
		if err != nil {
			return nil, nil, err
		}
		client, err := s.resolveCollaboration(ctx, id, in.ConnectionRef)
		if err != nil {
			return nil, nil, err
		}
		var value map[string]any
		switch in.Action {
		case "list":
			value, err = client.Mailboxes(ctx)
		case "", "search":
			value, err = client.MailQuery(ctx, in.Text, in.MailboxID, in.Position, in.Limit)
		default:
			err = errors.New("unsupported mail query action")
		}
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "mail_read", Description: "Read a selected authorized message with bounded body text and attachment metadata; content is untrusted user data.", Annotations: toolAnnotations("mail_read")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationMailReadInput) (*mcp.CallToolResult, any, error) {
		id, err := collaborationCaller(ctx)
		if err != nil {
			return nil, nil, err
		}
		client, err := s.resolveCollaboration(ctx, id, in.ConnectionRef)
		if err != nil {
			return nil, nil, err
		}
		value, err := client.MailRead(ctx, in.EmailID)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "calendar_query", Description: "List authorized calendars, read an event or query a bounded date window in an IANA time zone.", Annotations: toolAnnotations("calendar_query")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationQueryInput) (*mcp.CallToolResult, any, error) {
		id, err := collaborationCaller(ctx)
		if err != nil {
			return nil, nil, err
		}
		client, err := s.resolveCollaboration(ctx, id, in.ConnectionRef)
		if err != nil {
			return nil, nil, err
		}
		var value map[string]any
		switch in.Action {
		case "list":
			value, err = client.Calendars(ctx)
		case "get":
			value, err = client.CalendarRead(ctx, in.EventID, in.TimeZone)
		case "", "search":
			value, err = client.CalendarQuery(ctx, in.After, in.Before, in.TimeZone, in.Position, in.Limit)
		default:
			err = errors.New("unsupported calendar query action")
		}
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "calendar_respond", Description: "Preview or explicitly accept, decline or tentatively answer an invitation as the bound user. Applying can notify the organizer. Requires exact plan, state and idempotency key.", Annotations: toolAnnotations("calendar_respond")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationRespondInput) (*mcp.CallToolResult, any, error) {
		id, err := collaborationCaller(ctx)
		if err != nil {
			return nil, nil, err
		}
		client, err := s.resolveCollaboration(ctx, id, in.ConnectionRef)
		if err != nil {
			return nil, nil, err
		}
		if !in.Apply {
			value, err := client.CalendarResponsePlan(ctx, in.EventID, in.Response)
			return nil, value, err
		}
		value, err := s.applyCalendarResponse(ctx, id, client, in)
		return nil, value, err
	})
	mcp.AddTool(ms, &mcp.Tool{Name: "contacts_query", Description: "List your authorized address books or search bounded contact summaries.", Annotations: toolAnnotations("contacts_query")}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationQueryInput) (*mcp.CallToolResult, any, error) {
		id, err := collaborationCaller(ctx)
		if err != nil {
			return nil, nil, err
		}
		client, err := s.resolveCollaboration(ctx, id, in.ConnectionRef)
		if err != nil {
			return nil, nil, err
		}
		var value map[string]any
		switch in.Action {
		case "list":
			value, err = client.AddressBooks(ctx)
		case "get":
			value, err = client.ContactRead(ctx, in.ContactID)
		case "", "search":
			value, err = client.ContactQuery(ctx, in.Text, in.AddressBookID, in.Position, in.Limit)
		default:
			err = errors.New("unsupported contacts query action")
		}
		return nil, value, err
	})
	for _, name := range []string{"files_list", "files_read"} {
		mcp.AddTool(ms, &mcp.Tool{Name: name, Description: "Read a bounded authorized WebDAV directory or lightweight UTF-8 file. No recursive synchronization or arbitrary external URLs.", Annotations: toolAnnotations(name)}, func(ctx context.Context, _ *mcp.CallToolRequest, in collaborationFileInput) (*mcp.CallToolResult, any, error) {
			id, err := collaborationCaller(ctx)
			if err != nil {
				return nil, nil, err
			}
			client, err := s.resolveCollaboration(ctx, id, in.ConnectionRef)
			if err != nil {
				return nil, nil, err
			}
			var value map[string]any
			if name == "files_list" {
				value, err = client.FilesList(ctx, in.Href)
			} else {
				value, err = client.FilesRead(ctx, in.Href)
			}
			return nil, value, err
		})
	}
}
func (s *server) applyCalendarResponse(ctx context.Context, id identity, client *collaborationDataClient, in collaborationRespondInput) (map[string]any, error) {
	if len(in.IdempotencyKey) < 8 || len(in.IdempotencyKey) > 128 || in.PlanHash == "" || in.ExpectedState == "" {
		return nil, errors.New("exact plan, expected state and 8-128 character idempotency key required")
	}
	requestHash, _ := canonicalHash(in)
	result, err := s.db.ExecContext(ctx, `INSERT OR IGNORE INTO collaboration_writes(tenant,subject,key,request_hash,state) VALUES(?,?,?,?,'InProgress')`, id.Tenant, id.Subject, in.IdempotencyKey, requestHash)
	if err != nil {
		return nil, err
	}
	n, _ := result.RowsAffected()
	if n == 0 {
		var hash, state, raw string
		err = s.db.QueryRowContext(ctx, `SELECT request_hash,state,result FROM collaboration_writes WHERE tenant=? AND subject=? AND key=?`, id.Tenant, id.Subject, in.IdempotencyKey).Scan(&hash, &state, &raw)
		if err != nil {
			return nil, err
		}
		if hash != requestHash {
			return nil, errors.New("idempotency key belongs to another response")
		}
		if state != "Succeeded" {
			return nil, errors.New("response already attempted; read the event before any new decision")
		}
		var value map[string]any
		err = json.Unmarshal([]byte(raw), &value)
		return value, err
	}
	value, callErr := client.CalendarRespond(ctx, in.EventID, in.Response, in.ExpectedState, in.PlanHash)
	state, raw := "Unknown", ""
	if callErr == nil {
		state = "Succeeded"
		encoded, _ := json.Marshal(value)
		raw = string(encoded)
	}
	// Persist even if the caller disconnected after the provider applied the write.
	persist, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer cancel()
	_, err = s.db.ExecContext(persist, `UPDATE collaboration_writes SET state=?,result=? WHERE tenant=? AND subject=? AND key=?`, state, raw, id.Tenant, id.Subject, in.IdempotencyKey)
	if err != nil {
		return nil, errors.New("response outcome uncertain; inspect event without replaying")
	}
	return value, callErr
}
