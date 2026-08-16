#!/usr/bin/env sh
set -eu
: "${KUBECTL:=kubectl}"
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

$KUBECTL get --raw=/readyz >/dev/null
$KUBECTL get node restricted-node-1eb0570f74 restricted-node-296f36f2fb restricted-node-6dc3f140f9 >/dev/null
$KUBECTL apply -f "$dir/platform.yaml"
$KUBECTL -n ai-operations create configmap ai-operations-inventory --from-file=nodes.json="$dir/nodes.json" --dry-run=client -o yaml | $KUBECTL apply -f -
for deployment in cloud-platform-tool cluster-infra-tool agent-orchestra-collab; do
  $KUBECTL -n ai-operations rollout status deployment/$deployment --timeout=300s
done
$KUBECTL -n ai-operations get deployment,pod,service,pvc,httproute
