package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// Secretary receives evidence about transport state, never conversation bodies.
// The local outbox is retained until Secretary accepts each observation.
func recordA2AReceipt(ctx context.Context, db *sql.DB, taskRef, tenant, source, target, status, body string) error {
	if status != "queued" && status != "received" && status != "completed" {
		return fmt.Errorf("invalid receipt status")
	}
	identity := sha256.Sum256([]byte(taskRef + ":" + status))
	observationID := fmt.Sprintf("%x-%x-%x-%x-%x", identity[:4], identity[4:6], identity[6:8], identity[8:10], identity[10:16])
	contentHash := sha256.Sum256([]byte(body))
	observed := time.Now().UTC().Format(time.RFC3339Nano)
	payload := map[string]any{
		"observation_id": observationID, "interaction_id": taskRef,
		"native_type": "a2a-task", "native_status": status, "relation": "task_" + status,
		"source":      map[string]string{"kind": "agent_actor", "id": opaqueAgentActor(source)},
		"target":      map[string]string{"kind": "agent_actor", "id": opaqueAgentActor(target)},
		"runtime":     map[string]string{"kind": "runtime", "id": "tenant-mcp"},
		"observed_at": observed, "authority": "runtime_receipt",
		"evidence":        map[string]any{"strength": "authoritative", "failure_domain": "tenant-mcp", "method": "a2a-state-transition", "confidence": 1.0, "payload_hash": hex.EncodeToString(contentHash[:]), "media_type": "text/plain"},
		"idempotency_key": taskRef + ":" + status,
		"metadata":        map[string]string{"tenant_ref": tenant},
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	_, err = db.ExecContext(ctx, `INSERT OR IGNORE INTO a2a_receipts(task_ref,status,payload) VALUES(?,?,?)`, taskRef, status, string(raw))
	return err
}

func opaqueAgentActor(value string) string {
	if strings.HasPrefix(value, "agent_") || strings.HasPrefix(value, "task_") {
		return value
	}
	digest := sha256.Sum256([]byte(value))
	return "actor_" + hex.EncodeToString(digest[:16])
}

func flushA2AReceipt(ctx context.Context, db *sql.DB, taskRef, status string) string {
	var payload string
	var accepted sql.NullInt64
	if err := db.QueryRowContext(ctx, `SELECT payload,secretary_accepted_at FROM a2a_receipts WHERE task_ref=? AND status=?`, taskRef, status).Scan(&payload, &accepted); err != nil {
		return "missing"
	}
	if accepted.Valid {
		return "accepted"
	}
	url, secret := strings.TrimRight(os.Getenv("SECRETARY_OBSERVATION_URL"), "/"), os.Getenv("SECRETARY_WEBHOOK_SECRET")
	if url == "" || secret == "" {
		return "pending"
	}
	timestamp := fmt.Sprint(time.Now().Unix())
	signature := hmac.New(sha256.New, []byte(secret))
	signature.Write([]byte(timestamp + "." + payload))
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewBufferString(payload))
	if err != nil {
		return "pending"
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Secretary-Timestamp", timestamp)
	req.Header.Set("X-Secretary-Signature", hex.EncodeToString(signature.Sum(nil)))
	client := &http.Client{Timeout: 3 * time.Second}
	response, err := client.Do(req)
	if err != nil {
		return "pending"
	}
	defer response.Body.Close()
	io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	if response.StatusCode != http.StatusAccepted {
		return "pending"
	}
	_, err = db.ExecContext(ctx, `UPDATE a2a_receipts SET secretary_accepted_at=? WHERE task_ref=? AND status=? AND secretary_accepted_at IS NULL`, time.Now().Unix(), taskRef, status)
	if err != nil {
		return "pending"
	}
	return "accepted"
}

func flushA2AReceipts(ctx context.Context, db *sql.DB, taskRef string) map[string]string {
	result := map[string]string{}
	for _, status := range []string{"queued", "received", "completed"} {
		state := flushA2AReceipt(ctx, db, taskRef, status)
		if state != "missing" {
			result[status] = state
		}
	}
	return result
}
