package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"
)

// Only metadata lives in SQLite. Device codes and access tokens stay in the
// existing Kubernetes Credential Broker, never in MCP input/output or logs.
const collaborationDDL = `CREATE TABLE IF NOT EXISTS collaboration_connections (
 ref TEXT PRIMARY KEY, tenant TEXT NOT NULL, subject TEXT NOT NULL,
 request_hash TEXT NOT NULL, scopes TEXT NOT NULL, state TEXT NOT NULL,
 account_id TEXT NOT NULL DEFAULT '', expires INTEGER NOT NULL DEFAULT 0,
 verification_uri TEXT NOT NULL DEFAULT '', user_code TEXT NOT NULL DEFAULT '',
 next_poll INTEGER NOT NULL DEFAULT 0, poll_interval INTEGER NOT NULL DEFAULT 5);
 CREATE INDEX IF NOT EXISTS collaboration_owner ON collaboration_connections(tenant,subject);
 CREATE TABLE IF NOT EXISTS collaboration_writes (
 tenant TEXT NOT NULL, subject TEXT NOT NULL, key TEXT NOT NULL,
 request_hash TEXT NOT NULL, state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(tenant,subject,key));`

// Injectable only by in-process tests; production uses the existing Credential Broker transport.
var collaborationBrokerRequest = kubeRequest
var errCollaborationCredentialMissing = errors.New("collaboration credential missing")

func collaborationEnabled() bool { return os.Getenv("COLLABORATION_ENABLED") == "true" }
func collaborationOrigin() (string, error) {
	u, err := url.Parse(strings.TrimRight(os.Getenv("STALWART_URL"), "/"))
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		return "", errors.New("reviewed HTTPS Stalwart origin is required")
	}
	return u.String(), nil
}
func collaborationScopes(scopes []string) ([]string, string, error) {
	allowed := map[string]string{"mail.read": "mail", "calendar.read": "calendars", "calendar.respond": "calendars", "contacts.read": "contacts", "files.read": "files"}
	unique, native := map[string]bool{}, map[string]bool{}
	for _, scope := range scopes {
		service, ok := allowed[scope]
		if !ok {
			return nil, "", errors.New("unsupported collaboration scope")
		}
		unique[scope] = true
		native["urn:ietf:params:oauth:scope:"+service] = true
	}
	if len(unique) == 0 {
		return nil, "", errors.New("select at least one collaboration scope")
	}
	if unique["calendar.respond"] && !unique["calendar.read"] {
		return nil, "", errors.New("calendar.respond requires calendar.read")
	}
	out, provider := []string{}, []string{}
	for scope := range unique {
		out = append(out, scope)
	}
	for scope := range native {
		provider = append(provider, scope)
	}
	sort.Strings(out)
	sort.Strings(provider)
	return out, strings.Join(provider, " "), nil
}
func collaborationPlan(id identity, scopes []string) (map[string]any, error) {
	if id.Service || id.Tenant == "" || id.Subject == "" {
		return nil, errors.New("authenticated organization member required")
	}
	selected, _, err := collaborationScopes(scopes)
	if err != nil {
		return nil, err
	}
	origin, err := collaborationOrigin()
	if err != nil {
		return nil, err
	}
	hash, _ := canonicalHash(map[string]any{"tenant": id.Tenant, "subject": id.Subject, "scopes": selected, "origin": origin, "client": os.Getenv("STALWART_CLIENT_ID")})
	return map[string]any{"decision": "authorization_required", "planHash": hash, "scopes": selected, "providerOrigin": origin, "accountSource": "existing_user_authorization", "newStorageBytes": 0, "quotaImpact": "uses existing account quota", "retention": "provider data unchanged on disconnect", "approvalState": "user_browser_consent_required"}, nil
}

type collaborationConnection struct {
	Ref, Tenant, Subject, RequestHash, Scopes, State, AccountID string
	Expires                                                     int64
	VerificationURI, UserCode                                   string
	NextPoll, PollInterval                                      int64
}

const collaborationColumns = `ref,tenant,subject,request_hash,scopes,state,account_id,expires,verification_uri,user_code,next_poll,poll_interval`

func scanCollaboration(row rowScanner) (collaborationConnection, error) {
	var c collaborationConnection
	err := row.Scan(&c.Ref, &c.Tenant, &c.Subject, &c.RequestHash, &c.Scopes, &c.State, &c.AccountID, &c.Expires, &c.VerificationURI, &c.UserCode, &c.NextPoll, &c.PollInterval)
	return c, err
}
func (s *server) collaborationConnection(ctx context.Context, id identity, ref string) (collaborationConnection, error) {
	c, err := scanCollaboration(s.db.QueryRowContext(ctx, `SELECT `+collaborationColumns+` FROM collaboration_connections WHERE ref=? AND tenant=? AND subject=?`, ref, id.Tenant, id.Subject))
	if err != nil {
		return c, errors.New("collaboration connection not found")
	}
	return c, nil
}
func (c collaborationConnection) public() map[string]any {
	state := c.State
	if (state == "Ready" || state == "AuthorizationRequired") && c.Expires <= time.Now().Unix() {
		state = "Expired"
	}
	var scopes []string
	_ = json.Unmarshal([]byte(c.Scopes), &scopes)
	out := map[string]any{"connectionRef": c.Ref, "bindingRef": c.Ref, "state": state, "scopes": scopes, "expiresAt": c.Expires, "credentialMaterialReturned": false}
	if state == "AuthorizationRequired" {
		out["verificationUri"] = c.VerificationURI
		out["userCode"] = c.UserCode
		out["pollAfterSeconds"] = c.PollInterval
	}
	return out
}
func collaborationSecretPath(ref string) (string, error) {
	suffix := strings.TrimPrefix(ref, "collaboration://")
	if len(suffix) != 32 || strings.Trim(suffix, "0123456789abcdef") != "" {
		return "", errors.New("invalid connection reference")
	}
	namespace := os.Getenv("COLLABORATION_CREDENTIAL_NAMESPACE")
	if namespace == "" {
		namespace = os.Getenv("CREDENTIAL_NAMESPACE")
	}
	if namespace == "" {
		namespace = "credential-broker"
	}
	return "/api/v1/namespaces/" + url.PathEscape(namespace) + "/secrets/stalwart-" + suffix, nil
}
func collaborationSecret(ctx context.Context, id identity, c collaborationConnection) (map[string]any, map[string]string, error) {
	path, err := collaborationSecretPath(c.Ref)
	if err != nil {
		return nil, nil, err
	}
	secret, err := collaborationBrokerRequest(ctx, http.MethodGet, path, nil)
	if err != nil {
		var status kubeResponseError
		if errors.As(err, &status) && status.Status == 404 {
			return nil, nil, errCollaborationCredentialMissing
		}
		return nil, nil, errors.New("collaboration credential unavailable")
	}
	labels := object(object(secret["metadata"])["labels"])
	if labels["credentials.re8ch.com/tenant"] != id.Tenant || labels["credentials.re8ch.com/subject"] != tokenDigest(id.Subject)[:32] || labels["credentials.re8ch.com/provider"] != "stalwart" {
		return nil, nil, errors.New("collaboration credential owner mismatch")
	}
	data := map[string]string{}
	for k, v := range object(secret["data"]) {
		raw, e := base64.StdEncoding.DecodeString(fmt.Sprint(v))
		if e != nil {
			return nil, nil, errors.New("invalid collaboration credential")
		}
		data[k] = string(raw)
	}
	if data["subject"] != id.Subject || data["tenant"] != id.Tenant {
		return nil, nil, errors.New("collaboration credential owner mismatch")
	}
	return secret, data, nil
}
func saveCollaborationSecret(ctx context.Context, id identity, c collaborationConnection, previous map[string]any, data map[string]string) error {
	path, err := collaborationSecretPath(c.Ref)
	if err != nil {
		return err
	}
	data["tenant"] = id.Tenant
	data["subject"] = id.Subject
	meta := map[string]any{"name": "stalwart-" + strings.TrimPrefix(c.Ref, "collaboration://"), "labels": map[string]string{"credentials.re8ch.com/tenant": id.Tenant, "credentials.re8ch.com/subject": tokenDigest(id.Subject)[:32], "credentials.re8ch.com/provider": "stalwart"}}
	method := http.MethodPost
	target := path[:strings.LastIndex(path, "/")]
	if previous != nil {
		method = http.MethodPut
		target = path
		meta["resourceVersion"] = object(previous["metadata"])["resourceVersion"]
	}
	_, err = collaborationBrokerRequest(ctx, method, target, map[string]any{"apiVersion": "v1", "kind": "Secret", "metadata": meta, "type": "Opaque", "stringData": data})
	if err != nil {
		return errors.New("collaboration credential could not be stored")
	}
	return nil
}
func collaborationHTTP(ctx context.Context, path string, form url.Values, bearer string) (map[string]any, error) {
	origin, err := collaborationOrigin()
	if err != nil {
		return nil, err
	}
	method := http.MethodGet
	var body io.Reader
	if form != nil {
		method = http.MethodPost
		body = strings.NewReader(form.Encode())
	}
	req, err := http.NewRequestWithContext(ctx, method, origin+path, body)
	if err != nil {
		return nil, errors.New("invalid provider request")
	}
	req.Header.Set("Accept", "application/json")
	if form != nil {
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	client := &http.Client{Timeout: 20 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		return nil, errors.New("collaboration provider unavailable")
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, (1<<20)+1))
	if err != nil || len(raw) > 1<<20 {
		return nil, errors.New("provider response exceeds limit")
	}
	var result map[string]any
	if json.Unmarshal(raw, &result) != nil {
		return nil, errors.New("invalid provider response")
	}
	if resp.StatusCode != 200 {
		switch asString(result, "error") {
		case "authorization_pending", "slow_down", "access_denied", "expired_token":
			return map[string]any{"error": result["error"]}, nil
		}
		return nil, fmt.Errorf("collaboration provider returned HTTP %d", resp.StatusCode)
	}
	return result, nil
}
func (s *server) startCollaboration(ctx context.Context, id identity, scopes []string, planHash, key string) (map[string]any, error) {
	if !collaborationEnabled() {
		return nil, errors.New("collaboration is not published")
	}
	if len(key) < 8 || len(key) > 128 {
		return nil, errors.New("idempotency key must contain 8 to 128 characters")
	}
	plan, err := collaborationPlan(id, scopes)
	if err != nil {
		return nil, err
	}
	if planHash != plan["planHash"] {
		return nil, errors.New("review the current collaboration plan first")
	}
	ref := "collaboration://" + tokenDigest(id.Tenant + "\x00" + id.Subject + "\x00" + key)[:32]
	if old, e := s.collaborationConnection(ctx, id, ref); e == nil {
		if old.RequestHash != planHash {
			return nil, errors.New("idempotency key already used for different scopes")
		}
		return old.public(), nil
	}
	selected, native, _ := collaborationScopes(scopes)
	encoded, _ := json.Marshal(selected)
	clientID := os.Getenv("STALWART_CLIENT_ID")
	if clientID == "" {
		return nil, errors.New("reviewed provider OAuth client is required")
	}
	// Reserve before contacting the provider. Unknown outcomes are never replayed.
	_, err = s.db.ExecContext(ctx, `INSERT INTO collaboration_connections(ref,tenant,subject,request_hash,scopes,state) VALUES(?,?,?,?,?,'Starting')`, ref, id.Tenant, id.Subject, planHash, string(encoded))
	if err != nil {
		return nil, errors.New("connection request already started; read its status")
	}
	device, err := collaborationHTTP(ctx, "/auth/device", url.Values{"client_id": {clientID}, "scope": {native}}, "")
	if err != nil {
		return nil, err
	}
	origin, _ := collaborationOrigin()
	verification, e := url.Parse(asString(device, "verification_uri"))
	base, _ := url.Parse(origin)
	expires := int64(collaborationNumber(device["expires_in"]))
	interval := int64(collaborationNumber(device["interval"]))
	if interval < 5 {
		interval = 5
	}
	if e != nil || verification.Scheme != base.Scheme || verification.Host != base.Host || verification.User != nil || expires <= 0 || expires > 3600 || asString(device, "device_code") == "" || asString(device, "user_code") == "" {
		return nil, errors.New("invalid provider authorization response")
	}
	c := collaborationConnection{Ref: ref, Tenant: id.Tenant, Subject: id.Subject}
	err = saveCollaborationSecret(ctx, id, c, nil, map[string]string{"deviceCode": asString(device, "device_code"), "origin": origin, "clientID": clientID})
	if err != nil {
		return nil, err
	}
	_, err = s.db.ExecContext(ctx, `UPDATE collaboration_connections SET state='AuthorizationRequired',expires=?,verification_uri=?,user_code=?,poll_interval=?,next_poll=? WHERE ref=? AND state='Starting'`, time.Now().Unix()+expires, verification.String(), asString(device, "user_code"), interval, time.Now().Unix()+interval, ref)
	if err != nil {
		return nil, err
	}
	c, err = s.collaborationConnection(ctx, id, ref)
	return c.public(), err
}
func (s *server) completeCollaboration(ctx context.Context, id identity, ref string) (map[string]any, error) {
	c, err := s.collaborationConnection(ctx, id, ref)
	if err != nil {
		return nil, err
	}
	if c.State != "AuthorizationRequired" || c.Expires <= time.Now().Unix() {
		return c.public(), nil
	}
	// Atomic polling lease prevents concurrent exchanges and honors RFC 8628 pacing.
	now := time.Now().Unix()
	result, err := s.db.ExecContext(ctx, `UPDATE collaboration_connections SET state='Exchanging',next_poll=? WHERE ref=? AND state='AuthorizationRequired' AND next_poll<=?`, now+c.PollInterval+30, ref, now)
	if err != nil {
		return nil, err
	}
	n, _ := result.RowsAffected()
	if n != 1 {
		return nil, errors.New("authorization poll interval has not elapsed")
	}
	// A device code is single-use. Never exchange again after an unknown outcome.
	defer func() {
		cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		s.db.ExecContext(cleanup, `UPDATE collaboration_connections SET state='ReauthorizationRequired',verification_uri='',user_code='' WHERE ref=? AND state='Exchanging'`, ref)
	}()
	secret, data, err := collaborationSecret(ctx, id, c)
	if err != nil {
		return nil, err
	}
	origin, _ := collaborationOrigin()
	if data["origin"] != origin || data["clientID"] != os.Getenv("STALWART_CLIENT_ID") {
		return nil, errors.New("provider configuration changed; reconnect")
	}
	token, err := collaborationHTTP(ctx, "/auth/token", url.Values{"client_id": {data["clientID"]}, "device_code": {data["deviceCode"]}, "grant_type": {"urn:ietf:params:oauth:grant-type:device_code"}}, "")
	if err != nil {
		return nil, err
	}
	switch asString(token, "error") {
	case "authorization_pending", "slow_down":
		interval := c.PollInterval
		if token["error"] == "slow_down" {
			interval += 5
		}
		_, err = s.db.ExecContext(ctx, `UPDATE collaboration_connections SET state='AuthorizationRequired',next_poll=?,poll_interval=? WHERE ref=? AND state='Exchanging'`, time.Now().Unix()+interval, interval, ref)
		c.PollInterval = interval
		return c.public(), err
	case "access_denied", "expired_token":
		_, err = s.db.ExecContext(ctx, `UPDATE collaboration_connections SET state='Expired',user_code='',verification_uri='' WHERE ref=? AND state='Exchanging'`, ref)
		return map[string]any{"state": "Expired", "connectionRef": ref}, err
	}
	bearer := asString(token, "access_token")
	expires := int64(collaborationNumber(token["expires_in"]))
	if bearer == "" || !strings.EqualFold(asString(token, "token_type"), "Bearer") || expires <= 0 || expires > 86400 {
		return nil, errors.New("invalid scoped provider token")
	}
	var scopes []string
	_ = json.Unmarshal([]byte(c.Scopes), &scopes)
	_, expected, _ := collaborationScopes(scopes)
	granted := strings.Fields(asString(token, "scope"))
	sort.Strings(granted)
	if strings.Join(granted, " ") != expected {
		return nil, errors.New("provider did not return exactly the requested service scopes")
	}
	session, err := collaborationHTTP(ctx, "/jmap/session", nil, bearer)
	if err != nil {
		return nil, err
	}
	if _, management := object(session["capabilities"])["urn:stalwart:jmap"]; management {
		return nil, errors.New("unscoped management token cannot be used for collaboration")
	}
	accountID := ""
	for key, raw := range object(session["accounts"]) {
		account := object(raw)
		if account["isPersonal"] == true {
			if accountID != "" {
				return nil, errors.New("ambiguous personal provider account")
			}
			accountID = key
		}
	}
	if accountID == "" {
		return nil, errors.New("provider returned no personal account")
	}
	// Do not retain refresh tokens: an expired binding explicitly requires reauthorization.
	err = saveCollaborationSecret(ctx, id, c, secret, map[string]string{"accessToken": bearer, "origin": origin, "clientID": data["clientID"], "accountID": accountID})
	if err != nil {
		return nil, err
	}
	result, err = s.db.ExecContext(ctx, `UPDATE collaboration_connections SET state='Ready',account_id=?,expires=?,verification_uri='',user_code='' WHERE ref=? AND state='Exchanging'`, accountID, time.Now().Unix()+expires, ref)
	if err != nil {
		return nil, err
	}
	n, _ = result.RowsAffected()
	if n != 1 {
		return nil, errors.New("connection was revoked during authorization")
	}
	c, err = s.collaborationConnection(ctx, id, ref)
	return c.public(), err
}
func (s *server) resolveCollaboration(ctx context.Context, id identity, ref string) (*collaborationDataClient, error) {
	if !collaborationEnabled() {
		return nil, errors.New("collaboration is not published")
	}
	c, err := s.collaborationConnection(ctx, id, ref)
	if err != nil {
		return nil, err
	}
	if c.State != "Ready" || c.Expires <= time.Now().Unix() {
		return nil, errors.New("collaboration binding is revoked, expired or not ready")
	}
	_, data, err := collaborationSecret(ctx, id, c)
	if err != nil {
		return nil, err
	}
	origin, err := collaborationOrigin()
	if err != nil {
		return nil, err
	}
	if data["origin"] != origin || data["accountID"] != c.AccountID {
		return nil, errors.New("provider binding mismatch")
	}
	var scopes []string
	if json.Unmarshal([]byte(c.Scopes), &scopes) != nil {
		return nil, errors.New("invalid binding scopes")
	}
	// Numeric JMAP account ids are not assumed to be DAV path ids. The adapter
	// discovers/validates provider paths and enforces native sharing permissions.
	return newCollaborationDataClient(id, collaborationDataAccess{Tenant: id.Tenant, Subject: id.Subject, AccountID: c.AccountID, CredentialRef: c.Ref, Bearer: data["accessToken"], Scopes: scopes, ExpiresAt: time.Unix(c.Expires, 0)}, origin)
}
func (s *server) revokeCollaboration(ctx context.Context, id identity, ref string) (map[string]any, error) {
	c, err := s.collaborationConnection(ctx, id, ref)
	if err != nil {
		return nil, err
	}
	_, err = s.db.ExecContext(ctx, `UPDATE collaboration_connections SET state='Revoked',user_code='',verification_uri='' WHERE ref=? AND tenant=? AND subject=?`, ref, id.Tenant, id.Subject)
	if err != nil {
		return nil, err
	}
	// Deny future reads before removing material. A failed cleanup must never
	// restore Ready; repeating revoke safely retries cleanup without provider data deletion.
	secret, _, readErr := collaborationSecret(ctx, id, c)
	cleanup := "pending"
	if errors.Is(readErr, errCollaborationCredentialMissing) {
		cleanup = "removed"
	}
	if readErr == nil {
		path, _ := collaborationSecretPath(ref)
		_, err = collaborationBrokerRequest(ctx, http.MethodDelete, path, map[string]any{"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": map[string]any{"uid": object(secret["metadata"])["uid"]}})
		if err == nil {
			cleanup = "removed"
		}
	}
	return map[string]any{"connectionRef": ref, "state": "Revoked", "credentialCleanup": cleanup, "providerDataDeleted": false}, nil
}

func collaborationNumber(v any) float64 { n, _ := v.(float64); return n }
