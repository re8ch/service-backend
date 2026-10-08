package main

import (
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"
)

var usageID = regexp.MustCompile(`^ue_[a-f0-9]{32}$`)

func openLedger(path string) (*sql.DB, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	if _, err = db.Exec(`PRAGMA busy_timeout=10000; PRAGMA journal_mode=WAL; PRAGMA temp_store=MEMORY;
		CREATE TABLE IF NOT EXISTS instances(resource_ref TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, name TEXT NOT NULL, service_class TEXT NOT NULL, state TEXT NOT NULL, spec_json TEXT NOT NULL, generation INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(tenant_ref,name));
		CREATE TABLE IF NOT EXISTS operations(operation_id TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, resource_ref TEXT NOT NULL, action TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, before_json TEXT, after_json TEXT, result_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(tenant_ref,idempotency_key));
		CREATE TABLE IF NOT EXISTS bindings(binding_ref TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, resource_ref TEXT NOT NULL, binding_type TEXT NOT NULL, binding_json TEXT NOT NULL, revoked_at TEXT, created_at TEXT NOT NULL);
		CREATE TABLE IF NOT EXISTS received_usage(event_id TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, resource_ref TEXT NOT NULL, event_json TEXT NOT NULL, received_at TEXT NOT NULL);
		CREATE TABLE IF NOT EXISTS usage_outbox(event_id TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, resource_ref TEXT NOT NULL, event_json TEXT NOT NULL, delivered_at TEXT, created_at TEXT NOT NULL);
		CREATE INDEX IF NOT EXISTS received_usage_tenant_resource ON received_usage(tenant_ref, resource_ref);
		CREATE INDEX IF NOT EXISTS received_usage_tenant_received ON received_usage(tenant_ref, received_at);
		CREATE INDEX IF NOT EXISTS usage_outbox_tenant_resource ON usage_outbox(tenant_ref, resource_ref);
		CREATE TABLE IF NOT EXISTS cluster_registrations(registration_ref TEXT PRIMARY KEY, tenant_ref TEXT NOT NULL, cluster_uid TEXT NOT NULL, region_ref TEXT NOT NULL, issuer TEXT NOT NULL, public_key_hash TEXT NOT NULL, capabilities_json TEXT NOT NULL, state TEXT NOT NULL, enrollment_ref TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(tenant_ref,cluster_uid));
		CREATE TABLE IF NOT EXISTS cluster_connections(connection_ref TEXT PRIMARY KEY, registration_ref TEXT NOT NULL UNIQUE, tenant_ref TEXT NOT NULL, state TEXT NOT NULL, identity_ref TEXT NOT NULL, last_heartbeat TEXT, revoked_at TEXT, created_at TEXT NOT NULL);`); err != nil {
		db.Close()
		return nil, err
	}
	return db, nil
}

func parseTime(s string) (time.Time, error) {
	return time.Parse(time.RFC3339Nano, s)
}

func eventJSON(event map[string]any) ([]byte, error) {
	required := []string{"eventId", "tenantRef", "resourceRef", "serviceClass", "meter", "unit", "periodStart", "periodEnd", "regionRef", "sourceOperationId", "observedAt", "schemaVersion"}
	allowed := map[string]bool{"quantity": true, "dimensions": true, "pricingApplied": true, "accountingPosted": true}
	for _, key := range required {
		allowed[key] = true
		if value, ok := event[key].(string); !ok || value == "" {
			return nil, errors.New("invalid UsageEvent string field")
		}
	}
	for key := range event {
		if !allowed[key] {
			return nil, errors.New("invalid UsageEvent fields")
		}
	}
	if !usageID.MatchString(event["eventId"].(string)) || event["schemaVersion"] != "usage.re8ch.com/v1" {
		return nil, errors.New("invalid UsageEvent identity or version")
	}
	quantity, ok := event["quantity"].(float64)
	if !ok || math.IsNaN(quantity) || math.IsInf(quantity, 0) || quantity < 0 {
		return nil, errors.New("invalid UsageEvent quantity")
	}
	if event["pricingApplied"] != nil && event["pricingApplied"] != false || event["accountingPosted"] != nil && event["accountingPosted"] != false {
		return nil, errors.New("priced or posted events are not accepted")
	}
	if value, ok := event["dimensions"]; ok {
		if _, ok := value.(map[string]any); !ok {
			return nil, errors.New("invalid UsageEvent dimensions")
		}
	}
	start, err := parseTime(event["periodStart"].(string))
	if err != nil {
		return nil, errors.New("invalid UsageEvent period")
	}
	end, err := parseTime(event["periodEnd"].(string))
	if err != nil || !start.Before(end) {
		return nil, errors.New("invalid UsageEvent period")
	}
	if _, err = parseTime(event["observedAt"].(string)); err != nil {
		return nil, errors.New("invalid UsageEvent period")
	}
	return json.Marshal(event)
}

func (s *server) receiveUsage(event map[string]any) (map[string]any, error) {
	if s.ledger == nil {
		return nil, errors.New("usage ledger unavailable")
	}
	canonical, err := eventJSON(event)
	if err != nil {
		return nil, err
	}
	id, tenant, resource := event["eventId"].(string), event["tenantRef"].(string), event["resourceRef"].(string)
	tx, err := s.ledger.Begin()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var existing string
	err = tx.QueryRow(`SELECT event_json FROM received_usage WHERE event_id=?`, id).Scan(&existing)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	if err == nil {
		var old map[string]any
		if json.Unmarshal([]byte(existing), &old) != nil {
			return nil, errors.New("stored event is invalid")
		}
		previous, _ := json.Marshal(old)
		if string(previous) != string(canonical) {
			return nil, errors.New("eventId already exists with different content")
		}
		return map[string]any{"eventId": id, "accepted": true, "idempotentReplay": true}, nil
	}
	stamp := time.Now().UTC().Format("2006-01-02T15:04:05.000000Z")
	if _, err = tx.Exec(`INSERT INTO received_usage VALUES(?,?,?,?,?)`, id, tenant, resource, string(canonical), stamp); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return map[string]any{"eventId": id, "accepted": true, "idempotentReplay": false}, nil
}

func (s *server) billingUsage(tenant string, start, end time.Time) (map[string]any, error) {
	if s.ledger == nil {
		return nil, errors.New("usage ledger unavailable")
	}
	if !start.Before(end) || end.Sub(start) > 31*24*time.Hour {
		return nil, errors.New("billing window must be between 0 and 31 days")
	}
	lower, upper := start.UTC().Format("2006-01-02T15:04:05.000000Z"), end.UTC().Format("2006-01-02T15:04:05.000000Z")
	rows, err := s.ledger.Query(`SELECT json_extract(event_json, '$.serviceClass'), resource_ref,
		json_extract(event_json, '$.meter'), json_extract(event_json, '$.unit'),
		SUM(CAST(json_extract(event_json, '$.quantity') AS REAL)),
		MIN(json_extract(event_json, '$.periodStart')), MAX(json_extract(event_json, '$.periodEnd')), COUNT(*)
		FROM received_usage WHERE tenant_ref=? AND received_at>=? AND received_at<?
		GROUP BY 1,2,3,4 ORDER BY 1,2,3,4`, tenant, lower, upper)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var service, resource, meter, unit, periodStart, periodEnd string
		var quantity float64
		var count int
		if err = rows.Scan(&service, &resource, &meter, &unit, &quantity, &periodStart, &periodEnd, &count); err != nil {
			return nil, err
		}
		items = append(items, map[string]any{"serviceClass": service, "resourceRef": resource, "meter": meter, "unit": unit, "quantity": quantity, "periodStart": periodStart, "periodEnd": periodEnd, "eventCount": count})
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	return map[string]any{"tenantRef": tenant, "windowStart": lower, "windowEnd": upper, "pricingApplied": false, "accountingPosted": false, "items": items}, nil
}

func (s *server) usageSummary(tenant, resource string) (map[string]any, error) {
	if s.ledger == nil {
		return nil, errors.New("usage ledger unavailable")
	}
	// Received events supersede outbox copies with the same event ID. Aggregate
	// in SQLite so concurrent readers do not deserialize every event into Go
	// maps or hold the single ledger connection longer than necessary.
	query := `WITH events AS (
		SELECT event_json FROM received_usage WHERE tenant_ref=?`
	args := []any{tenant}
	if resource != "" {
		query += ` AND resource_ref=?`
		args = append(args, resource)
	}
	query += ` UNION ALL SELECT o.event_json FROM usage_outbox o WHERE o.tenant_ref=?`
	args = append(args, tenant)
	if resource != "" {
		query += ` AND o.resource_ref=?`
		args = append(args, resource)
	}
	query += ` AND NOT EXISTS (SELECT 1 FROM received_usage r WHERE r.event_id=o.event_id AND r.tenant_ref=o.tenant_ref`
	if resource != "" {
		query += ` AND r.resource_ref=?`
		args = append(args, resource)
	}
	query += `)) SELECT json_extract(event_json, '$.meter'), json_extract(event_json, '$.unit'),
		SUM(CAST(json_extract(event_json, '$.quantity') AS REAL)), COUNT(*)
		FROM events GROUP BY 1,2`
	rows, err := s.ledger.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	totals := map[string]float64{}
	eventCount := 0
	for rows.Next() {
		var meter, unit string
		var quantity float64
		var count int
		if err = rows.Scan(&meter, &unit, &quantity, &count); err != nil {
			return nil, err
		}
		totals[meter+":"+unit] = quantity
		eventCount += count
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	var resourceRef any
	if resource != "" {
		resourceRef = resource
	}
	return map[string]any{"tenantRef": tenant, "resourceRef": resourceRef, "totals": totals, "eventCount": eventCount, "pricingApplied": false, "accountingPosted": false}, nil
}

func usageCollectorAuthenticated(r *http.Request) (bool, error) {
	username := os.Getenv("TENANT_USAGE_CLIENT")
	header := r.Header.Get("Authorization")
	if username == "" || !strings.HasPrefix(header, "Bearer ") || len(header) > 16391 {
		return false, nil
	}
	host := os.Getenv("KUBERNETES_SERVICE_HOST")
	if host == "" {
		return false, nil
	}
	serviceToken, err := os.ReadFile("/var/run/secrets/kubernetes.io/serviceaccount/token")
	if err != nil {
		return false, err
	}
	ca, err := os.ReadFile("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
	if err != nil {
		return false, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(ca) {
		return false, errors.New("invalid Kubernetes CA")
	}
	port := os.Getenv("KUBERNETES_SERVICE_PORT")
	if port == "" {
		port = "443"
	}
	payload, _ := json.Marshal(map[string]any{"apiVersion": "authentication.k8s.io/v1", "kind": "TokenReview", "spec": map[string]any{"token": strings.TrimPrefix(header, "Bearer "), "audiences": []string{"re8ch-usage"}}})
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, fmt.Sprintf("https://%s:%s/apis/authentication.k8s.io/v1/tokenreviews", host, port), strings.NewReader(string(payload)))
	if err != nil {
		return false, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(serviceToken)))
	client := &http.Client{Timeout: 3 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}}}
	response, err := client.Do(req)
	if err != nil {
		return false, err
	}
	defer response.Body.Close()
	if response.StatusCode != 201 && response.StatusCode != 200 {
		return false, errors.New("Kubernetes TokenReview failed")
	}
	var result struct {
		Status struct {
			Authenticated bool `json:"authenticated"`
			User          struct {
				Username string `json:"username"`
			} `json:"user"`
			Audiences []string `json:"audiences"`
		} `json:"status"`
	}
	if err = json.NewDecoder(io.LimitReader(response.Body, 65536)).Decode(&result); err != nil {
		return false, err
	}
	for _, audience := range result.Status.Audiences {
		if audience == "re8ch-usage" && result.Status.Authenticated && result.Status.User.Username == username {
			return true, nil
		}
	}
	return false, nil
}
