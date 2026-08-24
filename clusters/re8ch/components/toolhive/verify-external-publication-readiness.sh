#!/usr/bin/env sh
set -u

: "${MCP_ORIGIN:=https://tools.re8ch.com:8443}"
: "${MCP_RESOURCE:=$MCP_ORIGIN/mcp}"
: "${AUTH_ORIGIN:=https://dex.re8ch.com}"
: "${AUTH_ISSUER:=$AUTH_ORIGIN/}"

failures=0

pass() { printf 'PASS: %s\n' "$*"; }
block() { printf 'BLOCKED: %s\n' "$*" >&2; failures=$((failures + 1)); }

check_https() {
  label=$1
  url=$2
  if curl --silent --show-error --max-time 10 --output /dev/null "$url"; then
    pass "$label HTTPS"
  else
    block "$label HTTPS/TLS"
  fi
}

check_https MCP "$MCP_ORIGIN/"
check_https authorization "$AUTH_ORIGIN/"

resource_tmp=$(mktemp)
auth_tmp=$(mktemp)
headers_tmp=$(mktemp)
trap 'rm -f "$resource_tmp" "$auth_tmp" "$headers_tmp"' EXIT INT TERM

if curl --silent --show-error --fail --max-time 10 \
  "$MCP_ORIGIN/.well-known/oauth-protected-resource/mcp" >"$resource_tmp"; then
  if jq -e --arg resource "$MCP_RESOURCE" '
    .resource == $resource and (.authorization_servers | type == "array" and length > 0)
  ' "$resource_tmp" >/dev/null 2>&1; then
    pass "RFC 9728 protected-resource metadata"
  else
    block "protected-resource metadata has wrong resource or authorization_servers"
  fi
else
  block "protected-resource metadata endpoint"
fi

auth_metadata_ok=false
for suffix in .well-known/oauth-authorization-server .well-known/openid-configuration; do
  metadata_url="${AUTH_ISSUER}${suffix}"
  if curl --silent --show-error --fail --max-time 10 "$metadata_url" >"$auth_tmp" && \
     jq -e '
       (.issuer | type == "string" and length > 0) and
       (.authorization_endpoint | type == "string" and startswith("https://")) and
       (.token_endpoint | type == "string" and startswith("https://")) and
       ((.code_challenge_methods_supported // []) | index("S256") != null)
     ' "$auth_tmp" >/dev/null 2>&1; then
    auth_metadata_ok=true
    pass "OAuth/OIDC discovery with PKCE S256 ($metadata_url)"
    break
  fi
done
[ "$auth_metadata_ok" = true ] || block "OAuth/OIDC discovery with authorization/token endpoints and PKCE S256"

http_code=$(curl --silent --show-error --max-time 10 --dump-header "$headers_tmp" \
  --header 'Accept: application/json, text/event-stream' \
  --header 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"readiness","version":"1"}}}' \
  --output /dev/null --write-out '%{http_code}' "$MCP_RESOURCE" || true)
[ -n "$http_code" ] || http_code=000
if [ "$http_code" = 401 ] && \
   grep -iq '^www-authenticate:.*resource_metadata=' "$headers_tmp"; then
  pass "unauthenticated MCP challenge"
else
  block "MCP must return 401 plus WWW-Authenticate resource_metadata (got $http_code)"
fi

if [ -n "${OPENAI_APPS_CHALLENGE_TOKEN:-}" ]; then
  challenge=$(curl --silent --show-error --fail --max-time 10 \
    "$MCP_ORIGIN/.well-known/openai-apps-challenge" 2>/dev/null || true)
  [ "$challenge" = "$OPENAI_APPS_CHALLENGE_TOKEN" ] && \
    pass "OpenAI domain challenge" || block "OpenAI domain challenge token"
else
  pass "OpenAI domain challenge not requested for this private Codex plugin"
fi

if [ "$failures" -eq 0 ]; then
  printf 'READY: external network and OAuth discovery gates passed\n'
  exit 0
fi

printf 'NOT READY: %s external publication gate(s) blocked\n' "$failures" >&2
exit 2
