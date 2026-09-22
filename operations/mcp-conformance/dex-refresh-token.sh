#!/usr/bin/env sh
set -eu

# Print only the short-lived access token. The rotating refresh token remains a
# protected GitHub environment secret and is never written to an artifact or command log.
: "${DEX_REFRESH_TOKEN:?DEX_REFRESH_TOKEN is required}"
: "${DEX_CLIENT_ID:?DEX_CLIENT_ID is required}"
: "${DEX_RESOURCE:?DEX_RESOURCE is required}"
: "${DEX_TOKEN_ENDPOINT:=https://dex.service.re8ch.com/token}"

response=$(curl --fail-with-body --silent --show-error --max-time 30 \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode grant_type=refresh_token \
  --data-urlencode "refresh_token=$DEX_REFRESH_TOKEN" \
  --data-urlencode "client_id=$DEX_CLIENT_ID" \
  --data-urlencode "resource=$DEX_RESOURCE" \
  "$DEX_TOKEN_ENDPOINT")

TOKEN_RESPONSE="$response" python3 - <<'PY'
import json, os
value = json.loads(os.environ["TOKEN_RESPONSE"])
token = value.get("access_token")
if not isinstance(token, str) or token.count(".") != 2:
    raise SystemExit("Dex refresh response did not contain a JWT access_token")
print(token)
PY
