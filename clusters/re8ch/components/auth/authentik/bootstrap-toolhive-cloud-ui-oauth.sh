#!/usr/bin/env bash
set -euo pipefail

: "${KUBE_SERVER:=https://192.0.2.176:6443}"
K=(kubectl --server="$KUBE_SERVER" --insecure-skip-tls-verify=true)
PORT="${AUTHENTIK_API_PORT:-29001}"
API="http://127.0.0.1:${PORT}/api/v3"

"${K[@]}" -n auth port-forward svc/authentik-server "${PORT}:80" \
  >/tmp/re8ch-authentik-cloud-ui-port-forward.log 2>&1 &
port_forward_pid=$!
trap 'kill "$port_forward_pid" 2>/dev/null || true' EXIT INT TERM

for _ in {1..20}; do
  curl -fsS "http://127.0.0.1:${PORT}/-/health/ready/" >/dev/null 2>&1 && break
  sleep 1
done

auth_token=$("${K[@]}" -n auth get secret authentik-bootstrap \
  -o jsonpath='{.data.AUTHENTIK_BOOTSTRAP_TOKEN}' | base64 -d)
auth_header="Authorization: Bearer ${auth_token}"

mappings=$(curl -fsS -H "$auth_header" \
  "$API/propertymappings/provider/scope/?page_size=100" |
  jq '[.results[] | select(.scope_name == "openid" or .scope_name == "profile" or .scope_name == "email" or .scope_name == "offline_access") | .pk]')

provider=$(curl -fsS -H "$auth_header" \
  "$API/providers/oauth2/?name=RE8CH%20ToolHive%20Cloud%20UI" | jq '.results[0] // null')
if [[ "$provider" == "null" ]]; then
  provider=$(jq -n \
    --arg authorization_flow 31f614ab-c41e-4729-88c6-f494ff5d4b9e \
    --arg invalidation_flow 3c9c5336-f7a1-43a7-acc8-c877a6d09d37 \
    --argjson property_mappings "$mappings" '{
      name: "RE8CH ToolHive Cloud UI",
      authorization_flow: $authorization_flow,
      invalidation_flow: $invalidation_flow,
      client_type: "confidential",
      client_id: "re8ch-toolhive-cloud-ui",
      grant_types: ["authorization_code", "refresh_token"],
      issuer_mode: "per_provider",
      include_claims_in_id_token: true,
      property_mappings: $property_mappings,
      redirect_uris: [{matching_mode: "strict", url: "https://tools.service.re8ch.com/api/auth/oauth2/callback/oidc"}]
    }' | curl -fsS -H "$auth_header" -H 'Content-Type: application/json' \
      -d @- "$API/providers/oauth2/")
fi

provider_pk=$(jq -r .pk <<<"$provider")
provider=$(jq -n '{redirect_uris: [{matching_mode: "strict", url: "https://tools.service.re8ch.com/api/auth/oauth2/callback/oidc"}]}' |
  curl -fsS -X PATCH -H "$auth_header" -H 'Content-Type: application/json' \
    -d @- "$API/providers/oauth2/${provider_pk}/")
client_secret=$(jq -r .client_secret <<<"$provider")
[[ -n "$client_secret" && "$client_secret" != "null" ]]

application_pk=$(curl -fsS -H "$auth_header" \
  "$API/core/applications/?slug=toolhive-cloud-ui" | jq -r '.results[0].pk // empty')
if [[ -z "$application_pk" ]]; then
  application_pk=$(jq -n --argjson provider "$provider_pk" '{
      name: "RE8CH Tool Catalog",
      slug: "toolhive-cloud-ui",
      provider: $provider,
      meta_description: "Official ToolHive visualization for the RE8CH MCP catalog",
      meta_launch_url: "https://tools.service.re8ch.com/",
      open_in_new_tab: true
    }' | curl -fsS -H "$auth_header" -H 'Content-Type: application/json' \
      -d @- "$API/core/applications/" | jq -r .pk)
fi
curl -fsS -X PATCH -H "$auth_header" -H 'Content-Type: application/json' \
  -d '{"meta_launch_url":"https://tools.service.re8ch.com/"}' \
  "$API/core/applications/${application_pk}/" >/dev/null

better_auth_secret=$(${K[@]} -n toolhive-system get secret toolhive-cloud-ui-auth \
  -o jsonpath='{.data.better-auth-secret}' 2>/dev/null | base64 -d || true)
if [[ -z "$better_auth_secret" ]]; then
  better_auth_secret=$(openssl rand -base64 48 | tr -d '\n')
fi
"${K[@]}" -n toolhive-system create secret generic toolhive-cloud-ui-auth \
  --from-literal=oidc-client-secret="$client_secret" \
  --from-literal=better-auth-secret="$better_auth_secret" \
  --dry-run=client -o yaml | "${K[@]}" apply -f -

printf 'provider=%s application=%s secret=toolhive-system/toolhive-cloud-ui-auth\n' \
  "$provider_pk" "$application_pk"
