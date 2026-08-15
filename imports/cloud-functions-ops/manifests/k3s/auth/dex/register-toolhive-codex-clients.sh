#!/usr/bin/env sh
set -eu

# Add the secretless PKCE clients used by Codex Desktop without exposing the
# existing connector credentials or password hashes in the repository/output.
: "${KUBECTL:=kubectl}"
: "${DEX_NAMESPACE:=dex}"
: "${DEX_SECRET:=dex-config}"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT HUP INT TERM

$KUBECTL -n "$DEX_NAMESPACE" get secret "$DEX_SECRET" -o jsonpath='{.data.config\.yaml}' \
  | base64 -d >"$workdir/config.yaml"

CONFIG_PATH="$workdir/config.yaml" python3 - <<'PY'
import os
from pathlib import Path
import yaml

path = Path(os.environ["CONFIG_PATH"])
config = yaml.safe_load(path.read_text())
clients = config.setdefault("staticClients", [])
desired = {
    "re8ch-platform-tools": "RE8CH Platform Tools",
    "re8ch-platform-admin": "RE8CH Platform Admin",
}
by_id = {client.get("id"): client for client in clients}
for client_id, name in desired.items():
    client = by_id.get(client_id)
    if client is None:
        client = {"id": client_id}
        clients.append(client)
    client.update({"name": name, "public": True})
    client.pop("secret", None)
    client.pop("redirectURIs", None)
path.write_text(yaml.safe_dump(config, sort_keys=False))
PY

backup="${DEX_SECRET}-pre-toolhive-dex"
$KUBECTL -n "$DEX_NAMESPACE" get secret "$DEX_SECRET" -o json \
  | jq --arg name "$backup" '
      .metadata = {name: $name, namespace: .metadata.namespace} |
      del(.metadata.creationTimestamp, .metadata.resourceVersion, .metadata.uid,
          .metadata.managedFields, .metadata.ownerReferences)' \
  | $KUBECTL apply -f - >/dev/null

$KUBECTL -n "$DEX_NAMESPACE" create secret generic "$DEX_SECRET" \
  --from-file=config.yaml="$workdir/config.yaml" --dry-run=client -o yaml \
  | $KUBECTL apply -f - >/dev/null
$KUBECTL -n "$DEX_NAMESPACE" rollout restart deployment/dex >/dev/null
$KUBECTL -n "$DEX_NAMESPACE" rollout status deployment/dex --timeout=180s

echo "Dex public clients registered; rollback Secret: $DEX_NAMESPACE/$backup"
