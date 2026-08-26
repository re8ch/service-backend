#!/usr/bin/env bash
set -euo pipefail
: "${KUBECTL:=kubectl}"
: "${NAMESPACE:=egress-fabric}"

if ${KUBECTL} -n "$NAMESPACE" get secret mihomos-anytls-users >/dev/null 2>&1; then
  echo "AnyTLS credential Secret already exists; no value was printed."
  exit 0
fi

password="$(openssl rand -hex 32)"
${KUBECTL} -n "$NAMESPACE" create secret generic mihomos-anytls-users --from-literal=password="$password"
unset password
echo "AnyTLS credential Secret created; its value was not printed."
