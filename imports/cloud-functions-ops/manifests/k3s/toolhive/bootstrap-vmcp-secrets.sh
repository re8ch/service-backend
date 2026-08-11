#!/usr/bin/env bash
set -euo pipefail

read -r -a KUBE_CMD <<<"${KUBECTL_COMMAND:-kubectl}"
NS=${TOOLHIVE_NAMESPACE:-toolhive-system}

k() { "${KUBE_CMD[@]}" "$@"; }

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT INT TERM

ensure_random_secret() {
  local name=$1 key=$2 bytes=$3
  if k -n "$NS" get secret "$name" >/dev/null 2>&1; then
    return
  fi
  # Environment-backed Secret values must be valid UTF-8 for the CRI runtime.
  openssl rand -hex "$bytes" | tr -d '\n' >"$tmp_dir/$key"
  k -n "$NS" create secret generic "$name" \
    --from-file="$key=$tmp_dir/$key" --dry-run=client -o yaml | k apply -f - >/dev/null
}

ensure_vmcp_crypto() {
  local name=$1
  if k -n "$NS" get secret "$name" >/dev/null 2>&1; then
    return
  fi
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$tmp_dir/private.pem" 2>/dev/null
  openssl rand 48 >"$tmp_dir/hmac"
  k -n "$NS" create secret generic "$name" \
    --from-file="private.pem=$tmp_dir/private.pem" \
    --from-file="hmac=$tmp_dir/hmac" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
}

ensure_client_secret_bundle() {
  local name=re8ch-vmcp-authentik-clients
  if k -n "$NS" get secret "$name" >/dev/null 2>&1; then
    return
  fi
  openssl rand -hex 32 | tr -d '\n' >"$tmp_dir/admin-client-secret"
  openssl rand -hex 32 | tr -d '\n' >"$tmp_dir/tools-client-secret"
  k -n "$NS" create secret generic "$name" \
    --from-file="admin-client-secret=$tmp_dir/admin-client-secret" \
    --from-file="tools-client-secret=$tmp_dir/tools-client-secret" \
    --dry-run=client -o yaml | k apply -f - >/dev/null
}

ensure_random_secret toolhive-auth-redis password 48
ensure_vmcp_crypto re8ch-admin-vmcp-crypto
ensure_vmcp_crypto re8ch-tools-vmcp-crypto
ensure_client_secret_bundle

printf '%s\n' \
  "$NS/toolhive-auth-redis keys: password" \
  "$NS/re8ch-admin-vmcp-crypto keys: private.pem,hmac" \
  "$NS/re8ch-tools-vmcp-crypto keys: private.pem,hmac" \
  "$NS/re8ch-vmcp-authentik-clients keys: admin-client-secret,tools-client-secret"
