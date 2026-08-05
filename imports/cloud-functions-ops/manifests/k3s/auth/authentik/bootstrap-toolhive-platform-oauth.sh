#!/usr/bin/env bash
set -euo pipefail

: "${KUBE_SERVER:=https://192.0.2.176:6443}"
K=(kubectl --server="$KUBE_SERVER" --insecure-skip-tls-verify=true)
PORT="${AUTHENTIK_API_PORT:-29000}"
API="http://127.0.0.1:${PORT}/api/v3"

"${K[@]}" -n auth port-forward svc/authentik-server "${PORT}:80" \
  >/tmp/re8ch-authentik-platform-oauth.log 2>&1 &
port_forward_pid=$!
trap 'kill "$port_forward_pid" 2>/dev/null || true' EXIT INT TERM

for _ in {1..30}; do
  curl -fsS "http://127.0.0.1:${PORT}/-/health/ready/" >/dev/null 2>&1 && break
  sleep 1
done

bootstrap_token=$("${K[@]}" -n auth get secret authentik-bootstrap \
  -o jsonpath='{.data.AUTHENTIK_BOOTSTRAP_TOKEN}' | base64 -d)
auth_header="Authorization: Bearer ${bootstrap_token}"

api_get() { curl -fsS -H "$auth_header" "$API$1"; }
api_post() { curl -fsS -H "$auth_header" -H 'Content-Type: application/json' -d @- "$API$1"; }
api_patch() { curl -fsS -X PATCH -H "$auth_header" -H 'Content-Type: application/json' -d @- "$API$1"; }

flow_uuid() {
  api_get "/flows/instances/?designation=$1&page_size=100" | jq -er '.results[0].pk'
}
authorization_flow=$(flow_uuid authorization)
invalidation_flow=$(flow_uuid invalidation)

base_mappings=$(api_get '/propertymappings/provider/scope/?page_size=100' | jq '[.results[] | select(.scope_name == "openid" or .scope_name == "profile" or .scope_name == "email") | .pk]')

ensure_group() {
  local name=$1
  local pk
  pk=$(api_get "/core/groups/?name=$(jq -rn --arg v "$name" '$v|@uri')" | jq -r --arg name "$name" '.results[] | select(.name == $name) | .pk' | head -1)
  if [[ -z "$pk" ]]; then
    pk=$(jq -n --arg name "$name" '{name:$name,is_superuser:false}' | api_post /core/groups/ | jq -er .pk)
  fi
  printf '%s' "$pk"
}

ensure_tier() {
  local tier=$1 audience=$2 title=$3 launch=$4
  local slug="toolhive-platform-${tier}" client_id="re8ch-platform-${tier}" scope="platform:${tier}"
  local scope_pk provider_pk app_pk mappings

  scope_pk=$(api_get "/propertymappings/provider/scope/?scope_name=$(jq -rn --arg v "$scope" '$v|@uri')" | jq -r --arg scope "$scope" '.results[] | select(.scope_name == $scope) | .pk' | head -1)
  if [[ -z "$scope_pk" ]]; then
    scope_pk=$(jq -n --arg tier "$tier" --arg scope "$scope" --arg audience "$audience" '{name:("RE8CH platform " + $tier),scope_name:$scope,description:("RE8CH ToolHive " + $tier + " permission"),expression:("return {\"aud\": \"" + $audience + "\"}")}' | api_post /propertymappings/provider/scope/ | jq -er .pk)
  fi
  mappings=$(jq -n --argjson base "$base_mappings" --arg scope "$scope_pk" '$base + [$scope]')

  provider_pk=$(api_get "/providers/oauth2/?client_id=$client_id" | jq -r --arg client_id "$client_id" '.results[] | select(.client_id == $client_id) | .pk' | head -1)
  if [[ -z "$provider_pk" ]]; then
    provider_pk=$(jq -n --arg title "$title" --arg client_id "$client_id" --arg authorization_flow "$authorization_flow" --arg invalidation_flow "$invalidation_flow" --argjson mappings "$mappings" '{name:$title,authorization_flow:$authorization_flow,invalidation_flow:$invalidation_flow,client_type:"public",client_id:$client_id,grant_types:["authorization_code","refresh_token"],issuer_mode:"per_provider",include_claims_in_id_token:true,property_mappings:$mappings,redirect_uris:[{matching_mode:"regex",url:"^https://chatgpt\\.com/connector/oauth/[A-Za-z0-9_-]+$"},{matching_mode:"regex",url:"^http://127\\.0\\.0\\.1:[0-9]+/.*$"},{matching_mode:"regex",url:"^http://localhost:[0-9]+/.*$"}]}' | api_post /providers/oauth2/ | jq -er .pk)
  else
    jq -n --argjson mappings "$mappings" '{property_mappings:$mappings}' | api_patch "/providers/oauth2/${provider_pk}/" >/dev/null
  fi

  # Applications with group bindings may be hidden from list responses even
  # for API administrators; direct slug retrieval remains authoritative.
  app_pk=$(api_get "/core/applications/${slug}/" 2>/dev/null | jq -r '.pk // empty' || true)
  if [[ -z "$app_pk" ]]; then
    app_pk=$(jq -n --arg title "$title" --arg slug "$slug" --arg launch "$launch" --argjson provider "$provider_pk" '{name:$title,slug:$slug,provider:$provider,meta_description:("OAuth access to " + $title),meta_launch_url:$launch,open_in_new_tab:true,policy_engine_mode:"any"}' | api_post /core/applications/ | jq -er .pk)
  else
    jq -n --arg launch "$launch" '{meta_launch_url:$launch,open_in_new_tab:true,policy_engine_mode:"any"}' | api_patch "/core/applications/${slug}/" >/dev/null
  fi
  printf '%s\t%s\t%s\n' "$scope_pk" "$provider_pk" "$app_pk"
}

operators_group=$(ensure_group 'RE8CH Platform Operators')
admins_group=$(ensure_group 'RE8CH Platform Admins')
observe_result=$(ensure_tier observe 'https://tools.re8ch.com/mcp' 'RE8CH Platform Observe' 'https://tools.re8ch.com/')
operate_result=$(ensure_tier operate 'https://operate.tools.re8ch.com/mcp' 'RE8CH Platform Operate' 'https://operate.tools.re8ch.com/mcp')
admin_result=$(ensure_tier admin 'https://admin.tools.re8ch.com/mcp' 'RE8CH Platform Admin' 'https://admin.tools.re8ch.com/mcp')

bind_group() {
  local app_pk=$1 group_pk=$2
  local exists
  exists=$(api_get "/policies/bindings/?target=$app_pk&group=$group_pk&page_size=100" | jq -r '.pagination.count // 0')
  if [[ "$exists" == 0 ]]; then
    jq -n --arg target "$app_pk" --arg group "$group_pk" '{target:$target,group:$group,enabled:true,order:0}' | api_post /policies/bindings/ >/dev/null
  fi
}

operate_app=$(cut -f3 <<<"$operate_result")
admin_app=$(cut -f3 <<<"$admin_result")
bind_group "$operate_app" "$operators_group"
bind_group "$operate_app" "$admins_group"
bind_group "$admin_app" "$admins_group"

# Create a non-interactive Authentik service identity once. Authentik returns the
# token only at creation time, so the Kubernetes Secret is the idempotency key.
if ! "${K[@]}" -n auth get secret toolhive-authentik-admin-api >/dev/null 2>&1; then
  service_user_pk=$(api_get '/core/users/?search=toolhive-platform-admin&page_size=100' | jq -r '.results[] | select(.name == "toolhive-platform-admin") | .pk' | head -1)
  if [[ -n "$service_user_pk" ]]; then
    service_token_id=$(api_get "/core/tokens/?user=$service_user_pk&page_size=100" | jq -r '.results[0].identifier // empty')
    service_token=$(api_get "/core/tokens/${service_token_id}/view_key/" | jq -er .key)
  else
    service_response=$(jq -n '{name:"toolhive-platform-admin",create_group:false}' | api_post /core/users/service_account/)
    service_token=$(jq -er '.token // .key' <<<"$service_response")
  fi
  "${K[@]}" -n auth create secret generic toolhive-authentik-admin-api \
    --from-literal=token="$service_token" --dry-run=client -o yaml | "${K[@]}" apply -f - >/dev/null
fi

jq -n --arg observe "$observe_result" --arg operate "$operate_result" --arg admin "$admin_result" --arg operators "$operators_group" --arg admins "$admins_group" '{observe:$observe,operate:$operate,admin:$admin,groups:{operators:$operators,admins:$admins},adminServiceIdentity:"toolhive-platform-admin"}'
