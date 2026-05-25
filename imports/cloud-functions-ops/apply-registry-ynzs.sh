#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-nuclio}"
REGISTRY_SERVER="${REGISTRY_SERVER:-registry.ynzs.com}"
SECRET_NAME="${SECRET_NAME:-registry-ynzs}"

if [[ -z "${REGISTRY_USERNAME:-}" || -z "${REGISTRY_PASSWORD:-}" ]]; then
  cat >&2 <<'EOF'
Missing registry credentials.

Usage:
  REGISTRY_USERNAME='robot$nuclio+nuclio-builder' \
  REGISTRY_PASSWORD='***' \
  ./nuclio/apply-registry-ynzs.sh
EOF
  exit 2
fi

kubectl -n "${NAMESPACE}" create secret docker-registry "${SECRET_NAME}" \
  --docker-server="${REGISTRY_SERVER}" \
  --docker-username="${REGISTRY_USERNAME}" \
  --docker-password="${REGISTRY_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

for service_account in default nuclio; do
  if kubectl -n "${NAMESPACE}" get serviceaccount "${service_account}" >/dev/null 2>&1; then
    kubectl -n "${NAMESPACE}" patch serviceaccount "${service_account}" --type=merge \
      -p "{\"imagePullSecrets\":[{\"name\":\"${SECRET_NAME}\"}]}" >/dev/null
    printf 'patched serviceaccount/%s\n' "${service_account}"
  fi
done

printf 'registry secret ready: %s/%s -> %s\n' "${NAMESPACE}" "${SECRET_NAME}" "${REGISTRY_SERVER}"
