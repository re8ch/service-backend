#!/usr/bin/env sh
set -eu

endpoint=https://tools.re8ch.com/tenant/mcp
metadata_url=https://tools.re8ch.com/.well-known/oauth-protected-resource/tenant/mcp
evidence=tenant-canary.evidence.json

# Always prove the new publication is routed, advertises the canonical resource,
# and rejects anonymous MCP calls. A protected refresh token upgrades the same
# job to full authenticated tool effectivity without making secret rotation a
# prerequisite for GitOps convergence.
metadata_file=$(mktemp)
response_file=$(mktemp)
trap 'rm -f "$metadata_file" "$response_file"' EXIT HUP INT TERM

curl --fail --silent --show-error --retry 30 --retry-all-errors --retry-delay 10 \
  --max-time 20 "$metadata_url" >"$metadata_file"
jq -e --arg resource "$endpoint" '.resource == $resource and (.authorization_servers | length > 0)' "$metadata_file" >/dev/null

anonymous_status=$(curl --silent --show-error --output "$response_file" --write-out '%{http_code}' \
  --max-time 20 -H 'Content-Type: application/json' -H 'Accept: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"tenant-canary","version":"1"}}}' \
  "$endpoint")
case "$anonymous_status" in 401|403) ;; *) echo "tenant MCP did not fail closed: HTTP $anonymous_status" >&2; exit 1 ;; esac

if [ -n "${TENANT_CANARY_REFRESH_TOKEN:-}" ]; then
  export DEX_REFRESH_TOKEN="$TENANT_CANARY_REFRESH_TOKEN"
  export DEX_CLIENT_ID=re8ch-tenant
  export DEX_RESOURCE="$endpoint"
  export TENANT_CANARY_TOKEN="$(./operations/mcp-conformance/dex-refresh-token.sh)"
  python3 operations/mcp-conformance/mcp_collection.py \
    operations/mcp-conformance/collections/tenant-canary.json --output "$evidence"
  unset TENANT_CANARY_TOKEN DEX_REFRESH_TOKEN
else
  jq -n --arg endpoint "$endpoint" --arg metadata "$metadata_url" --arg status "$anonymous_status" \
    --arg revision "${CI_COMMIT_SHA:-unknown}" \
    '{apiVersion:"evidence.re8ch.com/v1alpha1",kind:"MCPEvidence",collection:"tenant-canary",mode:"bootstrap-fail-closed",endpoint:$endpoint,protectedResourceMetadata:$metadata,anonymousStatus:($status|tonumber),gitRevision:$revision,sensitiveMaterialPersisted:false}' >"$evidence"
  cosign sign-blob --yes --identity-token "$MCP_EVIDENCE_SIGSTORE_ID_TOKEN" --bundle "$evidence.bundle" "$evidence"
fi
