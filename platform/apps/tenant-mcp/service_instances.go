package main

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

type rowScanner interface{ Scan(...any) error }

func scanInstance(row rowScanner) (map[string]any, error) {
	var ref, tenant, name, serviceClass, state, specJSON, created, updated string
	var generation int
	var deleted sql.NullString
	if err := row.Scan(&ref, &tenant, &name, &serviceClass, &state, &specJSON, &generation, &created, &updated, &deleted); err != nil {
		return nil, err
	}
	var spec map[string]any
	if err := json.Unmarshal([]byte(specJSON), &spec); err != nil {
		return nil, err
	}
	var deletedAt any
	if deleted.Valid {
		deletedAt = deleted.String
	}
	return map[string]any{"resource_ref": ref, "tenant_ref": tenant, "name": name, "service_class": serviceClass, "state": state, "spec": spec, "generation": generation, "created_at": created, "updated_at": updated, "deleted_at": deletedAt}, nil
}

const instanceColumns = `resource_ref,tenant_ref,name,service_class,state,spec_json,generation,created_at,updated_at,deleted_at`

func (s *server) instanceGet(tenant, ref string) (map[string]any, error) {
	if s.ledger == nil {
		return nil, errors.New("tenant state store unavailable")
	}
	result, err := scanInstance(s.ledger.QueryRow(`SELECT `+instanceColumns+` FROM instances WHERE resource_ref=? AND tenant_ref=?`, ref, tenant))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errors.New("service instance not found")
	}
	return result, err
}

func (s *server) instanceList(tenant string) ([]map[string]any, error) {
	rows, err := s.ledger.Query(`SELECT `+instanceColumns+` FROM instances WHERE tenant_ref=? ORDER BY created_at`, tenant)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []map[string]any{}
	for rows.Next() {
		item, err := scanInstance(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func (s *server) instancePlan(tenant, name string, spec map[string]any) (map[string]any, error) {
	checked, err := checkedSpec(spec, nil)
	if err != nil {
		return nil, err
	}
	hash, err := canonicalHash(checked)
	if err != nil {
		return nil, err
	}
	intent, err := provisioningIntent(tenant, name, checked)
	if err != nil {
		return nil, err
	}
	return map[string]any{"decision": "preview", "tenantRef": tenant, "resourceRef": "service://" + tenant + "/" + name, "requestHash": hash, "intent": intent, "credentialMaterialReturned": false}, nil
}

func (s *server) instanceMutate(tenant, name string, spec map[string]any, key, action string) (map[string]any, error) {
	if len(key) < 8 {
		return nil, errors.New("idempotencyKey must contain at least 8 characters")
	}
	ref := "service://" + tenant + "/" + name
	hash, err := canonicalHash(map[string]any{"action": action, "resourceRef": ref, "spec": spec})
	if err != nil {
		return nil, err
	}
	tx, err := s.ledger.Begin()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var storedResult, storedHash string
	err = tx.QueryRow(`SELECT result_json,request_hash FROM operations WHERE tenant_ref=? AND idempotency_key=?`, tenant, key).Scan(&storedResult, &storedHash)
	if err == nil {
		if storedHash != hash {
			return nil, errors.New("idempotencyKey was already used for a different request")
		}
		var result map[string]any
		if err = json.Unmarshal([]byte(storedResult), &result); err != nil {
			return nil, err
		}
		result["idempotentReplay"] = true
		return result, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	previous, err := scanInstance(tx.QueryRow(`SELECT `+instanceColumns+` FROM instances WHERE resource_ref=? AND tenant_ref=?`, ref, tenant))
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	if errors.Is(err, sql.ErrNoRows) {
		previous = nil
	}
	checked, err := checkedSpec(spec, previous)
	if err != nil {
		return nil, err
	}
	target := map[string]string{"Ready": "Provisioning", "Suspended": "Suspended", "Deleted": "Deleting"}[asString(checked, "desiredState")]
	generation := 1
	if previous != nil {
		generation = previous["generation"].(int) + 1
	}
	created := stamp()
	checkedJSON, err := canonicalJSON(checked)
	if err != nil {
		return nil, err
	}
	var deletedAt any
	if target == "Deleting" {
		deletedAt = created
	}
	if previous == nil {
		_, err = tx.Exec(`INSERT INTO instances VALUES(?,?,?,?,?,?,?,?,?,?)`, ref, tenant, name, checked["serviceClass"], target, string(checkedJSON), generation, created, created, deletedAt)
	} else {
		_, err = tx.Exec(`UPDATE instances SET service_class=?,state=?,spec_json=?,generation=?,updated_at=?,deleted_at=? WHERE resource_ref=? AND tenant_ref=?`, checked["serviceClass"], target, string(checkedJSON), generation, created, deletedAt, ref, tenant)
	}
	if err != nil {
		return nil, err
	}
	intent, err := provisioningIntent(tenant, name, checked)
	if err != nil {
		return nil, err
	}
	operationID := "op_" + newRef()
	result := map[string]any{"operationId": operationID, "tenantRef": tenant, "resourceRef": ref, "state": target, "generation": generation, "intent": intent, "credentialMaterialReturned": false}
	resultJSON, _ := canonicalJSON(result)
	var beforeJSON any
	if previous != nil {
		raw, err := canonicalJSON(previous)
		if err != nil {
			return nil, err
		}
		beforeJSON = string(raw)
	}
	_, err = tx.Exec(`INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?,?)`, operationID, tenant, ref, action, key, hash, beforeJSON, string(checkedJSON), string(resultJSON), created)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return result, nil
}

func (s *server) operationGet(tenant, id string) (map[string]any, error) {
	var operationID, tenantRef, resourceRef, action, key, hash, resultJSON, created string
	var beforeJSON, afterJSON sql.NullString
	err := s.ledger.QueryRow(`SELECT operation_id,tenant_ref,resource_ref,action,idempotency_key,request_hash,before_json,after_json,result_json,created_at FROM operations WHERE operation_id=? AND tenant_ref=?`, id, tenant).Scan(&operationID, &tenantRef, &resourceRef, &action, &key, &hash, &beforeJSON, &afterJSON, &resultJSON, &created)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errors.New("operation not found")
	}
	if err != nil {
		return nil, err
	}
	value := map[string]any{"operation_id": operationID, "tenant_ref": tenantRef, "resource_ref": resourceRef, "action": action, "idempotency_key": key, "request_hash": hash, "created_at": created, "before": nil, "after": nil, "result": nil}
	for name, raw := range map[string]sql.NullString{"before": beforeJSON, "after": afterJSON, "result": {String: resultJSON, Valid: true}} {
		if raw.Valid {
			var decoded any
			if err = json.Unmarshal([]byte(raw.String), &decoded); err != nil {
				return nil, err
			}
			value[name] = decoded
		}
	}
	return value, nil
}

func (s *server) bindingCreate(tenant, ref, bindingType string, parameters map[string]any) (map[string]any, error) {
	if _, err := s.instanceGet(tenant, ref); err != nil {
		return nil, err
	}
	for key := range parameters {
		if forbiddenCredentialField(key) {
			return nil, errors.New("credential material is forbidden")
		}
	}
	binding := "binding://" + tenant + "/" + newRef()
	value := map[string]any{"bindingRef": binding, "resourceRef": ref, "bindingType": bindingType, "accessRef": "access://tenant/" + tenant + "/" + newRef(), "credentialMaterialReturned": false}
	stored := map[string]any{}
	for key, item := range value {
		stored[key] = item
	}
	stored["parameters"] = parameters
	raw, err := canonicalJSON(stored)
	if err != nil {
		return nil, err
	}
	_, err = s.ledger.Exec(`INSERT INTO bindings VALUES(?,?,?,?,?,?,?)`, binding, tenant, ref, bindingType, string(raw), nil, stamp())
	return value, err
}

func (s *server) bindingList(tenant, ref string) ([]map[string]any, error) {
	query := `SELECT binding_json,revoked_at FROM bindings WHERE tenant_ref=?`
	var rows *sql.Rows
	var err error
	if ref == "" {
		rows, err = s.ledger.Query(query+` ORDER BY created_at`, tenant)
	} else {
		rows, err = s.ledger.Query(query+` AND resource_ref=? ORDER BY created_at`, tenant, ref)
	}
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []map[string]any{}
	for rows.Next() {
		var raw string
		var revoked sql.NullString
		if err = rows.Scan(&raw, &revoked); err != nil {
			return nil, err
		}
		var item map[string]any
		if err = json.Unmarshal([]byte(raw), &item); err != nil {
			return nil, err
		}
		item["revokedAt"] = nil
		if revoked.Valid {
			item["revokedAt"] = revoked.String
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func (s *server) bindingRevoke(tenant, ref string) (map[string]any, error) {
	now := stamp()
	result, err := s.ledger.Exec(`UPDATE bindings SET revoked_at=? WHERE binding_ref=? AND tenant_ref=? AND revoked_at IS NULL`, now, ref, tenant)
	if err != nil {
		return nil, err
	}
	count, _ := result.RowsAffected()
	if count != 1 {
		return nil, errors.New("active binding not found")
	}
	return map[string]any{"bindingRef": ref, "revokedAt": now, "credentialRevoked": true}, nil
}

func (s *server) usageEvents(tenant, ref string, limit int) (map[string]any, error) {
	if limit < 1 || limit > 500 {
		return nil, errors.New("limit must be between 1 and 500")
	}
	query := `SELECT event_json FROM received_usage WHERE tenant_ref=?`
	var rows *sql.Rows
	var err error
	if ref == "" {
		rows, err = s.ledger.Query(query+` ORDER BY received_at DESC,event_id DESC LIMIT ?`, tenant, limit)
	} else {
		rows, err = s.ledger.Query(query+` AND resource_ref=? ORDER BY received_at DESC,event_id DESC LIMIT ?`, tenant, ref, limit)
	}
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	events := []map[string]any{}
	for rows.Next() {
		var raw string
		if err = rows.Scan(&raw); err != nil {
			return nil, err
		}
		var event map[string]any
		if err = json.Unmarshal([]byte(raw), &event); err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	var resourceRef any
	if ref != "" {
		resourceRef = ref
	}
	return map[string]any{"tenantRef": tenant, "resourceRef": resourceRef, "events": events}, nil
}

func resourceName(tenant, ref string) (string, error) {
	prefix := "service://" + tenant + "/"
	if !strings.HasPrefix(ref, prefix) || len(ref) <= len(prefix) {
		return "", fmt.Errorf("resource reference is outside tenant %s", tenant)
	}
	return strings.TrimPrefix(ref, prefix), nil
}
