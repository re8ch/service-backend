#!/usr/bin/env bash
set -euo pipefail

: "${NETBIRD_DOMAIN:=access.re8ch.com}"
: "${NETBIRD_NAMESPACE:=netbird-access}"
: "${TLS_SOURCE_NAMESPACE:=llm-gateway}"
: "${KUBECTL:=sudo k3s kubectl}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

${KUBECTL} apply -f "$(dirname "$0")/namespace.yaml"

if ! ${KUBECTL} -n "$NETBIRD_NAMESPACE" get secret netbird-server-config >/dev/null 2>&1; then
  relay_secret="$(openssl rand -hex 32)"
  session_key="$(openssl rand -base64 32 | tr -d '\n')"
  # NetBird decodes this value as base64 and requires exactly 32 key bytes.
  store_key="$(openssl rand -base64 32 | tr -d '\n')"

  sed \
    -e "s|__NETBIRD_DOMAIN__|${NETBIRD_DOMAIN}|g" \
    -e "s|__RELAY_SECRET__|${relay_secret}|g" \
    -e "s|__SESSION_KEY__|${session_key}|g" \
    -e "s|__STORE_KEY__|${store_key}|g" \
    "$(dirname "$0")/config.yaml.template" >"$tmp_dir/config.yaml"

  ${KUBECTL} -n "$NETBIRD_NAMESPACE" create secret generic netbird-server-config \
    --from-file=config.yaml="$tmp_dir/config.yaml" \
    --dry-run=client -o yaml | ${KUBECTL} apply -f -
else
  echo "Keeping existing NetBird server keys."
fi

${KUBECTL} -n "$TLS_SOURCE_NAMESPACE" get secret re8ch-wildcard-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d >"$tmp_dir/tls.crt"
${KUBECTL} -n "$TLS_SOURCE_NAMESPACE" get secret re8ch-wildcard-tls -o jsonpath='{.data.tls\.key}' \
  | base64 -d >"$tmp_dir/tls.key"
${KUBECTL} -n "$NETBIRD_NAMESPACE" create secret tls re8ch-wildcard-tls \
  --cert="$tmp_dir/tls.crt" --key="$tmp_dir/tls.key" \
  --dry-run=client -o yaml | ${KUBECTL} apply -f -

echo "NetBird control-plane secrets are ready in ${NETBIRD_NAMESPACE}."
