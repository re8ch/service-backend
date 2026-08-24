#!/usr/bin/env sh
set -eu

: "${KUBECTL:=kubectl}"
: "${NAMESPACE:=ai-operations}"

key=$(openssl rand -hex 32)
$KUBECTL -n "$NAMESPACE" create secret generic marketplace-installer \
  --from-literal=state-hmac-key="$key" \
  --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
echo "Applied $NAMESPACE/marketplace-installer (key: state-hmac-key)"
