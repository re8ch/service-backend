#!/usr/bin/env bash
set -euo pipefail

: "${KUBE_SERVER:=https://192.0.2.18:6443}"
K=(kubectl --server="$KUBE_SERVER" --insecure-skip-tls-verify=true)
PORT="${AUTHENTIK_API_PORT:-29000}"
API="http://127.0.0.1:${PORT}/api/v3"

"${K[@]}" -n auth port-forward svc/authentik-server "${PORT}:80" \
  >/tmp/re8ch-authentik-api-port-forward.log 2>&1 &
port_forward_pid=$!
trap 'kill "$port_forward_pid" 2>/dev/null || true' EXIT INT TERM

for _ in {1..20}; do
  curl -fsS "http://127.0.0.1:${PORT}/-/health/ready/" >/dev/null 2>&1 && break
  sleep 1
done

auth_token=$("${K[@]}" -n auth get secret authentik-bootstrap \
  -o jsonpath='{.data.AUTHENTIK_BOOTSTRAP_TOKEN}' | base64 -d)
auth_header="Authorization: Bearer ${auth_token}"

scope_pk=$(curl -fsS -H "$auth_header" \
  "$API/propertymappings/provider/scope/?scope_name=platform%3Aread" |
  jq -r '.results[0].pk // empty')
if [[ -z "$scope_pk" ]]; then
  scope_pk=$(jq -n '{
      name: "RE8CH ToolHive public read",
      scope_name: "platform:read",
      description: "Read-only access to reviewed RE8CH public tools",
      expression: "return {\"aud\": \"https://tools.re8ch.com/mcp\"}"
    }' | curl -fsS -H "$auth_header" -H 'Content-Type: application/json' \
      -d @- "$API/propertymappings/provider/scope/" | jq -r .pk)
fi

provider_pk=$(curl -fsS -H "$auth_header" \
  "$API/providers/oauth2/?name=RE8CH%20ToolHive%20External" |
  jq -r '.results[0].pk // empty')
if [[ -z "$provider_pk" ]]; then
  mappings=$(curl -fsS -H "$auth_header" \
    "$API/propertymappings/provider/scope/?page_size=100" |
    jq --arg scope "$scope_pk" '[
      .results[] |
      select(.scope_name == "openid" or .scope_name == "profile" or .scope_name == "email") |
      .pk
    ] + [$scope]')
  provider_pk=$(jq -n \
    --arg authorization_flow 31f614ab-c41e-4729-88c6-f494ff5d4b9e \
    --arg invalidation_flow 3c9c5336-f7a1-43a7-acc8-c877a6d09d37 \
    --argjson property_mappings "$mappings" '{
      name: "RE8CH ToolHive External",
      authorization_flow: $authorization_flow,
      invalidation_flow: $invalidation_flow,
      client_type: "public",
      client_id: "re8ch-toolhive-external",
      grant_types: ["authorization_code", "refresh_token"],
      issuer_mode: "per_provider",
      include_claims_in_id_token: true,
      property_mappings: $property_mappings,
      redirect_uris: [
        {matching_mode: "regex", url: "^https://chatgpt\\.com/connector/oauth/[A-Za-z0-9_-]+$"},
        {matching_mode: "regex", url: "^http://127\\.0\\.0\\.1:[0-9]+/.*$"},
        {matching_mode: "regex", url: "^http://localhost:[0-9]+/.*$"}
      ]
    }' | curl -fsS -H "$auth_header" -H 'Content-Type: application/json' \
      -d @- "$API/providers/oauth2/" | jq -r .pk)
fi

application_pk=$(curl -fsS -H "$auth_header" \
  "$API/core/applications/?slug=toolhive-external" |
  jq -r '.results[0].pk // empty')
if [[ -z "$application_pk" ]]; then
  application_pk=$(jq -n --argjson provider "$provider_pk" '{
      name: "RE8CH External Tools",
      slug: "toolhive-external",
      provider: $provider,
      meta_description: "Read-only RE8CH tools published through ToolHive",
      meta_launch_url: "https://tools.re8ch.com/portal/",
      open_in_new_tab: true
    }' | curl -fsS -H "$auth_header" -H 'Content-Type: application/json' \
      -d @- "$API/core/applications/" | jq -r .pk)
fi

printf 'scope=%s provider=%s application=%s\n' \
  "$scope_pk" "$provider_pk" "$application_pk"
curl -fsS \
  "http://127.0.0.1:${PORT}/application/o/toolhive-external/.well-known/openid-configuration" |
  jq '{issuer, authorization_endpoint, token_endpoint, jwks_uri, code_challenge_methods_supported, scopes_supported}'
