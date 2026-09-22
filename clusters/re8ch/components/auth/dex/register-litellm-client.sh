#!/usr/bin/env sh
set -eu

: "${KUBECTL:=kubectl}"
: "${DEX_NAMESPACE:=dex}"
: "${DEX_SECRET:=dex-config}"
: "${LITELLM_NAMESPACE:=llm-gateway}"
: "${LITELLM_SECRET:=litellm-dex-sso}"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT HUP INT TERM

$KUBECTL -n "$DEX_NAMESPACE" get secret "$DEX_SECRET" -o jsonpath='{.data.config\.yaml}' \
  | base64 -d >"$workdir/config.yaml"

client_secret=$(openssl rand -hex 32)
CONFIG_PATH="$workdir/config.yaml" CLIENT_SECRET="$client_secret" python3 - <<'PY'
import os
from pathlib import Path
import yaml

path = Path(os.environ["CONFIG_PATH"])
config = yaml.safe_load(path.read_text())
clients = config.setdefault("staticClients", [])
desired = {
    "id": "litellm",
    "name": "LiteLLM Admin UI",
    "secret": os.environ["CLIENT_SECRET"],
    "redirectURIs": ["https://litellm.service.re8ch.com/sso/callback"],
}
for index, client in enumerate(clients):
    if client.get("id") == desired["id"]:
        clients[index] = desired
        break
else:
    clients.append(desired)
path.write_text(yaml.safe_dump(config, sort_keys=False))
PY

backup="${DEX_SECRET}-pre-litellm-sso"
$KUBECTL -n "$DEX_NAMESPACE" get secret "$DEX_SECRET" -o json \
  | jq --arg name "$backup" '
      .metadata = {name: $name, namespace: .metadata.namespace} |
      del(.metadata.creationTimestamp, .metadata.resourceVersion, .metadata.uid,
          .metadata.managedFields, .metadata.ownerReferences)' \
  | $KUBECTL apply -f - >/dev/null

$KUBECTL -n "$DEX_NAMESPACE" create secret generic "$DEX_SECRET" \
  --from-file=config.yaml="$workdir/config.yaml" --dry-run=client -o yaml \
  | $KUBECTL apply -f - >/dev/null

$KUBECTL -n "$LITELLM_NAMESPACE" create secret generic "$LITELLM_SECRET" \
  --from-literal=client-id=litellm \
  --from-literal=client-secret="$client_secret" \
  --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null

$KUBECTL -n "$DEX_NAMESPACE" rollout restart deployment/dex >/dev/null
$KUBECTL -n "$DEX_NAMESPACE" rollout status deployment/dex --timeout=180s
echo "Dex LiteLLM client registered; rollback Secret: $DEX_NAMESPACE/$backup"
