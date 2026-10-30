package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
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
func collaborationAuthHubEnabled() bool {
	return os.Getenv("COLLABORATION_AUTH_MODE") == "auth-hub-pkce"
}
func collaborationAuthorizationMode(requested string) (string, error) {
	if requested == "" {
		if collaborationAuthHubEnabled() {
			return "userhub", nil
		}
		return "device", nil
	}
	if requested != "userhub" && requested != "device" {
		return "", errors.New("authorization mode must be userhub or device")
	}
	if requested == "userhub" && !collaborationAuthHubEnabled() {
		return "", errors.New("User Hub collaboration authorization is not published")
	}
	return requested, nil
}
func collaborationOrigin() (string, error) {
	u, err := url.Parse(strings.TrimRight(os.Getenv("STALWART_URL"), "/"))
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		return "", errors.New("reviewed HTTPS Stalwart origin is required")
	}
	return u.String(), nil
}
func collaborationHTTPSURL(name, fallback string, allowPath bool) (string, error) {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		raw = fallback
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return "", fmt.Errorf("reviewed HTTPS %s is required", name)
	}
	if !allowPath && u.Path != "" {
		return "", fmt.Errorf("reviewed HTTPS %s origin is required", name)
	}
	return u.String(), nil
}
func collaborationAuthorizationURL() (string, error) {
	return collaborationHTTPSURL("STALWART_AUTHORIZATION_URL", "https://auth.re8ch.com/authorize", true)
}
func collaborationTokenURL() (string, error) {
	return collaborationHTTPSURL("STALWART_TOKEN_URL", "https://auth.re8ch.com/token", true)
}
func collaborationRedirectURL() (string, error) {
	return collaborationHTTPSURL("STALWART_REDIRECT_URI", "https://tools.service.re8ch.com/collaboration/oauth/callback", true)
}
func collaborationRandom() (string, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", errors.New("authorization entropy unavailable")
	}
	return base64.RawURLEncoding.EncodeToString(raw), nil
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
	return collaborationPlanMode(id, scopes, "")
}
func collaborationPlanMode(id identity, scopes []string, requestedMode string) (map[string]any, error) {
	if id.Service || id.Tenant == "" || id.Subject == "" {
		return nil, errors.New("authenticated organization member required")
	}
	mode, err := collaborationAuthorizationMode(requestedMode)
	if err != nil {
		return nil, err
	}
	selected, _, err := collaborationScopes(scopes)
	if err != nil {
		return nil, err
	}
	origin, err := collaborationOrigin()
	if err != nil {
		return nil, err
	}
	request := map[string]any{"tenant": id.Tenant, "subject": id.Subject, "scopes": selected, "origin": origin, "client": os.Getenv("STALWART_CLIENT_ID"), "authorizationMode": mode}
	approval := "user_browser_consent_required"
	if mode == "userhub" {
		authorization, authErr := collaborationAuthorizationURL()
		redirect, redirectErr := collaborationRedirectURL()
		if authErr != nil {
			return nil, authErr
		}
		if redirectErr != nil {
			return nil, redirectErr
		}
		request["authorization"] = authorization
		request["redirect"] = redirect
		approval = "auth_hub_browser_consent_required"
	}
	hash, _ := canonicalHash(request)
	return map[string]any{"decision": "authorization_required", "planHash": hash, "scopes": selected, "providerOrigin": origin, "authorizationMode": mode, "accountSource": "existing_user_authorization", "newStorageBytes": 0, "quotaImpact": "uses existing account quota", "retention": "provider data unchanged on disconnect", "approvalState": approval}, nil
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
func (s *server) collaborationConnectionByRef(ctx context.Context, ref string) (collaborationConnection, error) {
	c, err := scanCollaboration(s.db.QueryRowContext(ctx, `SELECT `+collaborationColumns+` FROM collaboration_connections WHERE ref=?`, ref))
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
		if c.UserCode != "" {
			out["userCode"] = c.UserCode
		} else {
			out["authorizationUri"] = c.VerificationURI
		}
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
	return s.startCollaborationMode(ctx, id, scopes, planHash, key, "")
}
func (s *server) startCollaborationMode(ctx context.Context, id identity, scopes []string, planHash, key, requestedMode string) (map[string]any, error) {
	mode, err := collaborationAuthorizationMode(requestedMode)
	if err != nil {
		return nil, err
	}
	if mode == "userhub" {
		return s.startAuthHubCollaboration(ctx, id, scopes, planHash, key, mode)
	}
	return s.startDeviceCollaboration(ctx, id, scopes, planHash, key, mode)
}
func (s *server) startDeviceCollaboration(ctx context.Context, id identity, scopes []string, planHash, key, mode string) (map[string]any, error) {
	if !collaborationEnabled() {
		return nil, errors.New("collaboration is not published")
	}
	if len(key) < 8 || len(key) > 128 {
		return nil, errors.New("idempotency key must contain 8 to 128 characters")
	}
	plan, err := collaborationPlanMode(id, scopes, mode)
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

func collaborationTokenHTTP(ctx context.Context, endpoint string, form url.Values) (map[string]any, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, strings.NewReader(form.Encode()))
	if err != nil {
		return nil, errors.New("invalid authorization server request")
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	client := &http.Client{Timeout: 20 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Do(req)
	if err != nil {
		return nil, errors.New("authorization server unavailable")
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, (1<<20)+1))
	if err != nil || len(raw) > 1<<20 {
		return nil, errors.New("authorization server response exceeds limit")
	}
	var result map[string]any
	if json.Unmarshal(raw, &result) != nil {
		return nil, errors.New("invalid authorization server response")
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("authorization server rejected the request: %s", asString(result, "error"))
	}
	return result, nil
}

func (s *server) startAuthHubCollaboration(ctx context.Context, id identity, scopes []string, planHash, key, mode string) (map[string]any, error) {
	if !collaborationEnabled() {
		return nil, errors.New("collaboration is not published")
	}
	if len(key) < 8 || len(key) > 128 {
		return nil, errors.New("idempotency key must contain 8 to 128 characters")
	}
	plan, err := collaborationPlanMode(id, scopes, mode)
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
	selected, _, _ := collaborationScopes(scopes)
	encoded, _ := json.Marshal(selected)
	clientID := strings.TrimSpace(os.Getenv("STALWART_CLIENT_ID"))
	if clientID == "" {
		return nil, errors.New("reviewed provider OAuth client is required")
	}
	authorizationEndpoint, err := collaborationAuthorizationURL()
	if err != nil {
		return nil, err
	}
	tokenEndpoint, err := collaborationTokenURL()
	if err != nil {
		return nil, err
	}
	redirectURI, err := collaborationRedirectURL()
	if err != nil {
		return nil, err
	}
	stateSecret, err := collaborationRandom()
	if err != nil {
		return nil, err
	}
	verifier, err := collaborationRandom()
	if err != nil {
		return nil, err
	}
	challenge := sha256.Sum256([]byte(verifier))
	state := strings.TrimPrefix(ref, "collaboration://") + "." + stateSecret
	authorization, _ := url.Parse(authorizationEndpoint)
	query := authorization.Query()
	query.Set("response_type", "code")
	query.Set("client_id", clientID)
	query.Set("redirect_uri", redirectURI)
	query.Set("scope", "openid profile email offline_access")
	query.Set("state", state)
	query.Set("code_challenge", base64.RawURLEncoding.EncodeToString(challenge[:]))
	query.Set("code_challenge_method", "S256")
	authorization.RawQuery = query.Encode()
	_, err = s.db.ExecContext(ctx, `INSERT INTO collaboration_connections(ref,tenant,subject,request_hash,scopes,state) VALUES(?,?,?,?,?,'Starting')`, ref, id.Tenant, id.Subject, planHash, string(encoded))
	if err != nil {
		return nil, errors.New("connection request already started; read its status")
	}
	c := collaborationConnection{Ref: ref, Tenant: id.Tenant, Subject: id.Subject}
	origin, _ := collaborationOrigin()
	err = saveCollaborationSecret(ctx, id, c, nil, map[string]string{
		"oauthState": state, "codeVerifier": verifier, "origin": origin,
		"clientID": clientID, "redirectURI": redirectURI, "tokenEndpoint": tokenEndpoint,
	})
	if err != nil {
		return nil, err
	}
	_, err = s.db.ExecContext(ctx, `UPDATE collaboration_connections SET state='AuthorizationRequired',expires=?,verification_uri=?,user_code='',poll_interval=5,next_poll=? WHERE ref=? AND state='Starting'`, time.Now().Unix()+900, authorization.String(), time.Now().Unix()+5, ref)
	if err != nil {
		return nil, err
	}
	c, err = s.collaborationConnection(ctx, id, ref)
	return c.public(), err
}

func (s *server) finishCollaborationToken(ctx context.Context, id identity, c collaborationConnection, secret map[string]any, data map[string]string, token map[string]any, retainRefresh bool) (map[string]any, error) {
	bearer := asString(token, "access_token")
	expires := int64(collaborationNumber(token["expires_in"]))
	if bearer == "" || !strings.EqualFold(asString(token, "token_type"), "Bearer") || expires <= 0 || expires > 86400 {
		return nil, errors.New("invalid scoped provider token")
	}
	if !retainRefresh {
		var scopes []string
		_ = json.Unmarshal([]byte(c.Scopes), &scopes)
		_, expected, _ := collaborationScopes(scopes)
		granted := strings.Fields(asString(token, "scope"))
		sort.Strings(granted)
		if strings.Join(granted, " ") != expected {
			return nil, errors.New("provider did not return exactly the requested service scopes")
		}
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
	if c.AccountID != "" && c.AccountID != accountID {
		return nil, errors.New("provider account changed during token renewal")
	}
	stored := map[string]string{"accessToken": bearer, "origin": data["origin"], "clientID": data["clientID"], "accountID": accountID}
	if retainRefresh {
		stored["tokenEndpoint"] = data["tokenEndpoint"]
		stored["redirectURI"] = data["redirectURI"]
		stored["refreshToken"] = asString(token, "refresh_token")
		if stored["refreshToken"] == "" {
			stored["refreshToken"] = data["refreshToken"]
		}
	}
	if err = saveCollaborationSecret(ctx, id, c, secret, stored); err != nil {
		return nil, err
	}
	result, err := s.db.ExecContext(ctx, `UPDATE collaboration_connections SET state='Ready',account_id=?,expires=?,verification_uri='',user_code='' WHERE ref=? AND state='Exchanging'`, accountID, time.Now().Unix()+expires, c.Ref)
	if err != nil {
		return nil, err
	}
	n, _ := result.RowsAffected()
	if n != 1 {
		return nil, errors.New("connection was revoked during authorization")
	}
	c, err = s.collaborationConnection(ctx, id, c.Ref)
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

func collaborationCallbackPage(w http.ResponseWriter, status int, title, message string) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	fmt.Fprintf(w, `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>%s</title><style>body{font:16px system-ui;max-width:42rem;margin:12vh auto;padding:0 1.5rem;color:#17202a}main{border:1px solid #d8dee4;border-radius:14px;padding:2rem}h1{font-size:1.45rem}</style></head><body><main><h1>%s</h1><p>%s</p></main></body></html>`, title, title, message)
}

func collaborationRefFromState(state string) (string, bool) {
	parts := strings.Split(state, ".")
	if len(parts) != 2 || len(parts[0]) != 32 || len(parts[1]) != 43 || strings.Trim(parts[0], "0123456789abcdef") != "" {
		return "", false
	}
	if _, err := base64.RawURLEncoding.DecodeString(parts[1]); err != nil {
		return "", false
	}
	return "collaboration://" + parts[0], true
}

func (s *server) collaborationOAuthCallback(w http.ResponseWriter, r *http.Request) {
	if !collaborationEnabled() || !collaborationAuthHubEnabled() {
		collaborationCallbackPage(w, http.StatusNotFound, "Authorization unavailable", "User Hub collaboration authorization is not published.")
		return
	}
	state := r.URL.Query().Get("state")
	ref, ok := collaborationRefFromState(state)
	if !ok {
		collaborationCallbackPage(w, http.StatusBadRequest, "Authorization failed", "The authorization state is invalid or expired.")
		return
	}
	c, err := s.collaborationConnectionByRef(r.Context(), ref)
	if err != nil {
		collaborationCallbackPage(w, http.StatusBadRequest, "Authorization failed", "The collaboration request was not found.")
		return
	}
	id := identity{Tenant: c.Tenant, Subject: c.Subject}
	if c.State == "Ready" {
		collaborationCallbackPage(w, http.StatusOK, "Authorization complete", "This Re8ch collaboration connection is already ready. You can return to Re8ch.")
		return
	}
	if c.State != "AuthorizationRequired" || c.Expires <= time.Now().Unix() {
		collaborationCallbackPage(w, http.StatusBadRequest, "Authorization expired", "Return to Re8ch and start a new collaboration connection.")
		return
	}
	secret, data, err := collaborationSecret(r.Context(), id, c)
	if err != nil || len(data["oauthState"]) != len(state) || subtle.ConstantTimeCompare([]byte(data["oauthState"]), []byte(state)) != 1 {
		collaborationCallbackPage(w, http.StatusBadRequest, "Authorization failed", "The authorization state could not be verified.")
		return
	}
	if r.URL.Query().Get("error") != "" || r.URL.Query().Get("code") == "" {
		s.db.ExecContext(r.Context(), `UPDATE collaboration_connections SET state='ReauthorizationRequired',verification_uri='',user_code='' WHERE ref=? AND state='AuthorizationRequired'`, ref)
		collaborationCallbackPage(w, http.StatusBadRequest, "Authorization declined", "No mailbox access was granted. Return to Re8ch to try again.")
		return
	}
	result, err := s.db.ExecContext(r.Context(), `UPDATE collaboration_connections SET state='Exchanging' WHERE ref=? AND state='AuthorizationRequired'`, ref)
	if err != nil {
		collaborationCallbackPage(w, http.StatusInternalServerError, "Authorization failed", "The connection could not be updated.")
		return
	}
	n, _ := result.RowsAffected()
	if n != 1 {
		collaborationCallbackPage(w, http.StatusConflict, "Authorization in progress", "This authorization response is already being processed.")
		return
	}
	finished := false
	defer func() {
		if !finished {
			cleanup, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), 5*time.Second)
			defer cancel()
			s.db.ExecContext(cleanup, `UPDATE collaboration_connections SET state='ReauthorizationRequired',verification_uri='',user_code='' WHERE ref=? AND state='Exchanging'`, ref)
		}
	}()
	tokenEndpoint, tokenErr := collaborationTokenURL()
	redirectURI, redirectErr := collaborationRedirectURL()
	if tokenErr != nil || redirectErr != nil || data["tokenEndpoint"] != tokenEndpoint || data["redirectURI"] != redirectURI || data["clientID"] != os.Getenv("STALWART_CLIENT_ID") {
		collaborationCallbackPage(w, http.StatusConflict, "Authorization changed", "The provider configuration changed. Return to Re8ch and reconnect.")
		return
	}
	token, err := collaborationTokenHTTP(r.Context(), tokenEndpoint, url.Values{
		"grant_type":    {"authorization_code"},
		"client_id":     {data["clientID"]},
		"code":          {r.URL.Query().Get("code")},
		"redirect_uri":  {redirectURI},
		"code_verifier": {data["codeVerifier"]},
	})
	if err != nil {
		collaborationCallbackPage(w, http.StatusBadGateway, "Authorization failed", "Auth Hub did not complete the mailbox authorization. Return to Re8ch and try again.")
		return
	}
	if _, err = s.finishCollaborationToken(r.Context(), id, c, secret, data, token, true); err != nil {
		collaborationCallbackPage(w, http.StatusBadGateway, "Mailbox connection failed", "Auth Hub signed in successfully, but Stalwart did not accept the account token.")
		return
	}
	finished = true
	collaborationCallbackPage(w, http.StatusOK, "Authorization complete", "Your Stalwart mailbox, calendar and files are connected. You can return to Re8ch.")
}

func (s *server) refreshCollaboration(ctx context.Context, id identity, c collaborationConnection) (collaborationConnection, error) {
	result, err := s.db.ExecContext(ctx, `UPDATE collaboration_connections SET state='Exchanging' WHERE ref=? AND tenant=? AND subject=? AND state='Ready'`, c.Ref, id.Tenant, id.Subject)
	if err != nil {
		return c, err
	}
	n, _ := result.RowsAffected()
	if n != 1 {
		return s.collaborationConnection(ctx, id, c.Ref)
	}
	finished := false
	defer func() {
		if !finished {
			cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
			defer cancel()
			s.db.ExecContext(cleanup, `UPDATE collaboration_connections SET state='ReauthorizationRequired',verification_uri='',user_code='' WHERE ref=? AND state='Exchanging'`, c.Ref)
		}
	}()
	secret, data, err := collaborationSecret(ctx, id, c)
	if err != nil {
		return c, err
	}
	tokenEndpoint, err := collaborationTokenURL()
	if err != nil || data["tokenEndpoint"] != tokenEndpoint || data["clientID"] != os.Getenv("STALWART_CLIENT_ID") || data["refreshToken"] == "" {
		return c, errors.New("collaboration authorization must be renewed in User Hub")
	}
	token, err := collaborationTokenHTTP(ctx, tokenEndpoint, url.Values{
		"grant_type":    {"refresh_token"},
		"client_id":     {data["clientID"]},
		"refresh_token": {data["refreshToken"]},
	})
	if err != nil {
		return c, err
	}
	if _, err = s.finishCollaborationToken(ctx, id, c, secret, data, token, true); err != nil {
		return c, err
	}
	finished = true
	return s.collaborationConnection(ctx, id, c.Ref)
}

func (s *server) resolveCollaboration(ctx context.Context, id identity, ref string) (*collaborationDataClient, error) {
	if !collaborationEnabled() {
		return nil, errors.New("collaboration is not published")
	}
	c, err := s.collaborationConnection(ctx, id, ref)
	if err != nil {
		return nil, err
	}
	if c.State == "Ready" && c.Expires <= time.Now().Unix()+60 {
		c, err = s.refreshCollaboration(ctx, id, c)
		if err != nil {
			return nil, err
		}
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
