#!/usr/bin/env bash
set -euo pipefail

SITE_ID="${ESA_SITE_ID:-158897763809500}"
API_POOL_ID="${ESA_API_POOL_ID:-158904865282276}"
ENDPOINT="${ESA_ENDPOINT:-esa.cn-hangzhou.aliyuncs.com}"
API_HOST="${API_HOST:-api.jz.ynzs.com}"
MGR_RO_HOST="${MGR_RO_HOST:-mgr-ro.jz.ynzs.com}"
ORIGIN="${ORIGIN:-https://jz.ynzs.com}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

fail() {
  echo "verify_ynzs_esa_cdn=fail reason=$*" >&2
  exit 1
}

need_header() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if ! grep -Eiq "$pattern" "$file"; then
    echo "missing_header=$label" >&2
    sed -n '1,80p' "$file" >&2
    exit 1
  fi
}

curl_status() {
  local output="$1"
  shift
  curl -sS -D "$output.headers" -o "$output.body" -w '%{http_code}' "$@"
}

expect_status_retry() {
  local label="$1"
  local want="$2"
  local attempts="${3:-3}"
  local min_successes="${4:-2}"
  shift 4
  local successes=0
  local statuses=()
  for ((i = 1; i <= attempts; i++)); do
    local output="$tmpdir/${label//[^A-Za-z0-9_.-]/_}-$i"
    local status
    status="$(curl_status "$output" "$@" || true)"
    statuses+=("${status:-curl_error}")
    if [[ "$status" == "$want" ]]; then
      successes=$((successes + 1))
    fi
  done
  if (( successes < min_successes )); then
    fail "$label expected $want successes=$successes/$attempts statuses=${statuses[*]}"
  fi
  echo "$label=$want successes=$successes/$attempts"
}

fetch_status_retry() {
  local label="$1"
  local want="$2"
  local attempts="${3:-3}"
  local min_successes="${4:-2}"
  shift 4
  local successes=0
  local statuses=()
  local first_success=""
  for ((i = 1; i <= attempts; i++)); do
    local output="$tmpdir/${label//[^A-Za-z0-9_.-]/_}-$i"
    local status
    status="$(curl_status "$output" "$@" || true)"
    statuses+=("${status:-curl_error}")
    if [[ "$status" == "$want" ]]; then
      successes=$((successes + 1))
      if [[ -z "$first_success" ]]; then
        first_success="$output"
      fi
    fi
  done
  if (( successes < min_successes )); then
    fail "$label expected $want successes=$successes/$attempts statuses=${statuses[*]}"
  fi
  echo "$first_success"
}

echo "site_id=$SITE_ID api_pool_id=$API_POOL_ID"

cache_json="$tmpdir/cache-rules.json"
aliyun esa ListCacheRules --SiteId "$SITE_ID" --endpoint "$ENDPOINT" > "$cache_json"
python3 - "$cache_json" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
configs = data.get("Configs") or []
by_name = {item.get("RuleName"): item for item in configs}
public = by_name.get("qianwen-public-rpc-cache")
private_groups = {
    "qianwen-private-rpc-user-cache": {
        "/rpc/me",
        "/rpc/user_get",
        "/rpc/user_invite_info",
        "/rpc/user_model_config_get",
        "/rpc/user_search_preference_get",
        "/rpc/creator_profile_get",
        "/rpc/billing_my",
        "/rpc/billing_credit_summary",
    },
    "qianwen-private-rpc-project-cache": {
        "/rpc/project_list",
        "/rpc/project_single",
        "/rpc/project_count",
        "/rpc/project_asset_overview",
        "/rpc/project_visibility_list",
        "/rpc/storyline_get",
        "/rpc/script_list_with_elements_paged",
        "/rpc/storyboard_list_paged",
        "/rpc/comments_list",
    },
    "qianwen-private-rpc-assets-cache": {
        "/rpc/assets_list_paged",
        "/rpc/assets_get_image",
        "/rpc/user_generated_assets_list",
        "/rpc/public_assets_history",
        "/rpc/public_assets_search_text",
        "/rpc/public_asset_visibility_get",
    },
    "qianwen-private-rpc-config-cache": {
        "/rpc/setting_get",
        "/rpc/setting_ai_model_list",
        "/rpc/setting_ai_model_map",
        "/rpc/setting_video_manufacturers",
        "/rpc/video_config_list",
        "/rpc/video_edit_capabilities",
        "/rpc/video_list",
        "/rpc/video_prompt_context_get",
        "/rpc/prompt_get_prompts",
        "/rpc/art_style_get",
        "/rpc/changelog_list",
    },
}
bypass = by_name.get("qianwen-mgr-ro-rpc-bypass")
if not public or not bypass:
    raise SystemExit("missing qianwen mgr-ro cache rules")
missing_private = [name for name in private_groups if name not in by_name]
if missing_private:
    raise SystemExit(f"missing qianwen private cache rules: {missing_private}")

public_allowed = {
    "/rpc/homepage_reference_project",
    "/rpc/public_assets_list",
    "/rpc/setting_image_model_options",
    "/rpc/setting_text_models",
    "/rpc/setting_video_model_list",
    "/rpc/billing_model_price",
}
private_allowed = set().union(*private_groups.values())
for name, rule in [("public", public.get("Rule", ""))]:
    if "/rpc/public_assets_search_text" in rule:
        raise SystemExit(f"{name} rule still contains public_assets_search_text")
    missing = sorted(item for item in public_allowed if item not in rule)
    if missing:
        raise SystemExit(f"{name} rule missing allowlist entries: {missing}")
    leaked_private = sorted(item for item in private_allowed if item in rule)
    if leaked_private:
        raise SystemExit(f"{name} rule contains private entries: {leaked_private}")

for group_name, expected in private_groups.items():
    group = by_name[group_name]
    rule = group.get("Rule", "")
    missing = sorted(item for item in expected if item not in rule)
    if missing:
        raise SystemExit(f"{group_name} missing entries: {missing}")
    if group.get("IncludeHeader") != "authorization cookie":
        raise SystemExit(f"{group_name} must include authorization cookie in cache key")
    if group.get("EdgeCacheTtl") != "30" or group.get("BrowserCacheMode") != "no_cache":
        raise SystemExit(f"{group_name} has unexpected private cache TTL/browser mode")
    if group.get("EdgeStatusCodeCacheTtl") != "4xx=0,5xx=0":
        raise SystemExit(f"{group_name} must keep 4xx/5xx cache TTL at 0")

bypass_rule = bypass.get("Rule", "")
for item in sorted(public_allowed | private_allowed):
    if item not in bypass_rule:
        raise SystemExit(f"bypass rule missing cached RPC exclusion: {item}")

if public.get("EdgeCacheTtl") != "60" or public.get("BrowserCacheTtl") != "30":
    raise SystemExit("unexpected public cache TTL")
if public.get("EdgeStatusCodeCacheTtl") != "4xx=0,5xx=0":
    raise SystemExit("4xx/5xx cache TTL must stay 0")
if bypass.get("BypassCache") != "bypass_all" or bypass.get("EdgeCacheMode") != "no_cache":
    raise SystemExit("bypass rule is not no-cache")
print("mgr_ro_cache_rules=ok")
PY

pool_json="$tmpdir/api-origin-pool.json"
aliyun esa GetOriginPool --SiteId "$SITE_ID" --Id "$API_POOL_ID" --endpoint "$ENDPOINT" > "$pool_json"
python3 - "$pool_json" "$API_HOST" > "$tmpdir/enabled-origins.tsv" <<'PY'
import json
import sys

pool = json.load(open(sys.argv[1]))
api_host = sys.argv[2]
origins = pool.get("Origins") or []
enabled = []
for origin in origins:
    if origin.get("Enabled"):
        host_headers = (origin.get("Header") or {}).get("Host") or []
        if api_host not in host_headers:
            raise SystemExit(f"origin {origin.get('Name')} missing Host header {api_host}")
        enabled.append(origin)
if not enabled:
    raise SystemExit("api origin pool has no enabled origins")
for origin in enabled:
    print(f"{origin.get('Name')}\t{origin.get('Address')}\t{origin.get('Weight')}")
print(f"api_enabled_origins={len(enabled)}", file=sys.stderr)
PY
cat "$tmpdir/enabled-origins.tsv"

public_output="$(fetch_status_retry "public-assets-list" 200 5 2 \
  "https://$MGR_RO_HOST/rpc/public_assets_list?p_type=image&p_page=1&p_page_size=3" \
  -H 'accept: application/json' -H 'Accept-Profile: api' --connect-timeout 5 --max-time 12)"
need_header "$public_output.headers" '^X-Site-Cache-Status: (HIT|MISS|EXPIRED)' public_assets_list_cache
need_header "$public_output.headers" '^X-Swift-CacheTime: 60' public_assets_list_ttl
echo "mgr_ro_public_assets_list=ok"

private_output="$(fetch_status_retry "private-me-no-auth" 401 5 2 \
  "https://$MGR_RO_HOST/rpc/me" \
  -H 'accept: application/json' -H 'Accept-Profile: api' --connect-timeout 5 --max-time 12)"
need_header "$private_output.headers" '^X-Swift-CacheTime: 0' private_me_error_ttl_zero
need_header "$private_output.headers" '^Cache-Control: no-cache' private_me_no_cache
echo "mgr_ro_private_error_no_cache=ok"

while IFS=$'\t' read -r name address weight; do
  [[ -n "$name" && -n "$address" ]] || continue
  ip="$(dig +short "$address" A | head -1)"
  [[ -n "$ip" ]] || fail "cannot resolve origin $name address=$address"
  resolve=(--resolve "$API_HOST:443:$ip")

  expect_status_retry "$name.region" 200 3 2 \
    --noproxy '*' "${resolve[@]}" "https://$API_HOST/_region" --connect-timeout 5 --max-time 12

  expect_status_retry "$name.oss_options" 204 3 2 \
    --noproxy '*' "${resolve[@]}" -X OPTIONS "https://$API_HOST/other/ossSts" \
    -H "Origin: $ORIGIN" -H 'Access-Control-Request-Method: POST' \
    -H 'Access-Control-Request-Headers: content-type,x-trace-id' \
    --connect-timeout 5 --max-time 12

  expect_status_retry "$name.events_options" 204 3 2 \
    --noproxy '*' "${resolve[@]}" -X OPTIONS "https://$API_HOST/client-events" \
    -H "Origin: $ORIGIN" -H 'Access-Control-Request-Method: POST' \
    -H 'Access-Control-Request-Headers: content-type,x-trace-id' \
    --connect-timeout 5 --max-time 12

  expect_status_retry "$name.homepage_exact" 200 3 2 \
    --noproxy '*' "${resolve[@]}" "https://$API_HOST/homepage" --connect-timeout 5 --max-time 12

  echo "api_origin=$name address=$address weight=$weight status=ok"
done < "$tmpdir/enabled-origins.tsv"

echo "verify_ynzs_esa_cdn=ok"
