#!/usr/bin/env bash
set -euo pipefail

: "${POLICY_PATHS_FILE:?Path to private paths.json}"
: "${POLICY_ED25519_KEY_FILE:?Path to raw/base64 Ed25519 private key}"
: "${KUBECTL:=kubectl}"
: "${APPLY:=false}"

if [[ "${APPLY}" != "true" ]]; then
  echo "Validated input paths. Set APPLY=true after cluster preflight to install without printing secret bytes." >&2
  exit 0
fi

${KUBECTL} -n egress-fabric create secret generic network-decomplexer-policy-secrets \
  --from-file=paths.json="${POLICY_PATHS_FILE}" \
  --from-file=ed25519-private-key="${POLICY_ED25519_KEY_FILE}" \
  --dry-run=client -o yaml \
  | ${KUBECTL} apply -f - >/dev/null
echo "network-decomplexer-policy-secrets installed" >&2
