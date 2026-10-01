package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const redeemPage = `<!doctype html><meta charset="utf-8"><title>RE8CH Credential Delivery</title>
<style>body{font:16px system-ui;max-width:680px;margin:64px auto;padding:0 24px}button,pre{box-sizing:border-box;width:100%;padding:14px;border:1px solid #777;border-radius:10px}pre{white-space:pre-wrap;word-break:break-all;background:#eee}</style>
<h1>一次性凭据交付</h1><p>凭据只显示一次，页面不会缓存。请确认当前设备可信。</p><button id="redeem">显示凭据</button><pre id="output">尚未兑换</pre>
<script>document.getElementById('redeem').onclick=async()=>{const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);const r=await fetch('/credentials/redeem',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token})});document.getElementById('output').textContent=r.ok?JSON.stringify(await r.json(),null,2):'凭据无效、已使用或已过期';document.getElementById('redeem').disabled=true}</script>`

const adoptPage = `<!doctype html><meta charset="utf-8"><title>Adopt Harbor Credential</title>
<style>body{font:16px system-ui;max-width:680px;margin:64px auto;padding:0 24px}input,button,pre{box-sizing:border-box;width:100%;padding:12px;margin:6px 0}pre{white-space:pre-wrap;background:#eee}</style><h1>导入现有 Harbor 登录</h1><p>密码通过本次 HTTPS 请求直接进入 Credential Broker，不进入 MCP 或 AI 上下文。</p><input id="username" autocomplete="username" placeholder="Harbor username"><input id="password" type="password" autocomplete="current-password" placeholder="Harbor password"><button id="adopt">验证并导入</button><pre id="output"></pre><script>document.getElementById('adopt').onclick=async()=>{const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);const username=document.getElementById('username').value,password=document.getElementById('password').value;document.getElementById('password').value='';const r=await fetch('/credentials/adopt',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token,username,password})});document.getElementById('output').textContent=JSON.stringify(await r.json(),null,2);document.getElementById('adopt').disabled=true}</script>`

func credentialPage(w http.ResponseWriter, value string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
	_, _ = io.WriteString(w, value)
}

func (s *server) redeemHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Pragma", "no-cache")
	r.Body = http.MaxBytesReader(w, r.Body, 8192)
	var payload struct {
		Token string `json:"token"`
	}
	if json.NewDecoder(r.Body).Decode(&payload) != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid_redemption"})
		return
	}
	reference, tenant, err := s.consumeRedemption(payload.Token, "credential_redemptions")
	if err != nil {
		writeJSON(w, 410, map[string]string{"error": "expired_or_consumed"})
		return
	}
	if err = readyHumanCredential(r.Context(), tenant, reference); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid_redemption"})
		return
	}
	provider, material, err := credentialMaterial(r.Context(), tenant, reference)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid_redemption"})
		return
	}
	serviceURL := os.Getenv("HARBOR_URL")
	if serviceURL == "" {
		serviceURL = "https://registry.re8ch.com"
	}
	if provider == "grafana" {
		serviceURL = os.Getenv("GRAFANA_URL")
		if serviceURL == "" {
			serviceURL = "https://grafana.service.re8ch.com/grafana"
		}
	}
	value := map[string]any{"tenant": tenant, "provider": provider, "serviceUrl": serviceURL}
	for key, item := range material {
		value[key] = item
	}
	writeJSON(w, 200, value)
}

func catalogHarbor(tenant string) (string, string, error) {
	path := os.Getenv("HARBOR_TENANTS_PATH")
	if path == "" {
		path = "/etc/re8ch-ai-operations/harbor-tenants.json"
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", "", err
	}
	var catalog struct {
		Tenants map[string]struct {
			Harbor struct {
				Project     string `json:"project"`
				WebUsername string `json:"webUsername"`
			} `json:"harbor"`
		} `json:"tenants"`
	}
	if err = json.Unmarshal(raw, &catalog); err != nil {
		return "", "", err
	}
	entry, ok := catalog.Tenants[tenant]
	if !ok {
		return "", "", errors.New("tenant not declared")
	}
	project, username := entry.Harbor.Project, entry.Harbor.WebUsername
	if project == "" {
		project = tenant
	}
	if username == "" {
		username = tenant
	}
	if !tenantName.MatchString(project) || !tenantName.MatchString(username) {
		return "", "", errors.New("invalid Harbor account declaration")
	}
	return project, username, nil
}

func harborHasProject(ctx *http.Request, username, password, project string) (bool, error) {
	base := strings.TrimRight(os.Getenv("HARBOR_URL"), "/")
	if base == "" {
		base = "https://registry.re8ch.com"
	}
	client := &http.Client{Timeout: 30 * time.Second}
	for page := 1; page <= 100; page++ {
		req, err := http.NewRequestWithContext(ctx.Context(), http.MethodGet, base+"/api/v2.0/projects?page="+fmt.Sprint(page)+"&page_size=100", nil)
		if err != nil {
			return false, err
		}
		req.SetBasicAuth(username, password)
		req.Header.Set("Accept", "application/json")
		response, err := client.Do(req)
		if err != nil {
			return false, err
		}
		if response.StatusCode != 200 {
			response.Body.Close()
			return false, errors.New("Harbor login rejected")
		}
		var projects []struct {
			Name string `json:"name"`
		}
		err = json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&projects)
		response.Body.Close()
		if err != nil {
			return false, err
		}
		for _, item := range projects {
			if item.Name == project {
				return true, nil
			}
		}
		if len(projects) < 100 {
			return false, nil
		}
	}
	return false, errors.New("Harbor project pagination limit")
}

func (s *server) adoptHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	r.Body = http.MaxBytesReader(w, r.Body, 16384)
	var payload struct {
		Token    string `json:"token"`
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if json.NewDecoder(r.Body).Decode(&payload) != nil {
		writeJSON(w, 400, map[string]string{"error": "adoption_failed"})
		return
	}
	_, tenant, err := s.consumeRedemption(payload.Token, "credential_adoptions")
	if err != nil {
		writeJSON(w, 410, map[string]string{"error": "expired_or_consumed"})
		return
	}
	project, expected, err := catalogHarbor(tenant)
	if err != nil || payload.Username != expected || payload.Password == "" {
		writeJSON(w, 400, map[string]string{"error": "adoption_failed"})
		return
	}
	allowed, err := harborHasProject(r, payload.Username, payload.Password, project)
	if err != nil || !allowed {
		writeJSON(w, 400, map[string]string{"error": "adoption_failed"})
		return
	}
	random := make([]byte, 16)
	if _, err = rand.Read(random); err != nil {
		writeJSON(w, 503, map[string]string{"error": "adoption_failed"})
		return
	}
	id := hex.EncodeToString(random)
	namespace := os.Getenv("CREDENTIAL_NAMESPACE")
	if namespace == "" {
		namespace = "credential-broker"
	}
	manifest := map[string]any{"apiVersion": "v1", "kind": "Secret", "metadata": map[string]any{"name": "harbor-" + id, "namespace": namespace, "labels": map[string]string{"credentials.re8ch.com/provider": "harbor", "credentials.re8ch.com/tenant": tenant, "credentials.re8ch.com/project": project, "credentials.re8ch.com/type": "human"}}, "type": "Opaque", "stringData": map[string]string{"username": payload.Username, "password": payload.Password, "project": project}}
	if _, err = kubeRequest(r.Context(), http.MethodPost, "/api/v1/namespaces/"+url.PathEscape(namespace)+"/secrets", manifest); err != nil {
		writeJSON(w, 400, map[string]string{"error": "adoption_failed"})
		return
	}
	writeJSON(w, 200, map[string]any{"tenant": tenant, "credential_ref": "credential://harbor/" + tenant + "/" + id, "credential_material_returned": false})
}
