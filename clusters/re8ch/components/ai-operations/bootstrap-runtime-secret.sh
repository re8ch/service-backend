#!/usr/bin/env sh
set -eu

: "${KUBECTL:=kubectl}"
: "${KUBECONFIG_SOURCE:?set KUBECONFIG_SOURCE to the reviewed kubeconfig file}"
: "${SSH_CONFIG_SOURCE:?set SSH_CONFIG_SOURCE to the reviewed SSH config file}"
: "${SSH_PRIVATE_KEY_SOURCE:?set SSH_PRIVATE_KEY_SOURCE to the permanent jump-host private key file}"

$KUBECTL -n ai-operations create secret generic cloud-platform-runtime \
  --from-file=kubeconfig-re8ch.yaml="$KUBECONFIG_SOURCE" \
  --from-file=ssh-config="$SSH_CONFIG_SOURCE" \
  --from-file=id_ed25519="$SSH_PRIVATE_KEY_SOURCE" \
  --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
echo "Updated Secret reference: ai-operations/cloud-platform-runtime (kubeconfig-re8ch.yaml, ssh-config, id_ed25519)"
