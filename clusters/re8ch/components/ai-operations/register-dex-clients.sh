#!/usr/bin/env sh
set -eu

: "${KUBECTL:=kubectl}"
: "${DEX_NAMESPACE:=dex}"
: "${DEX_SECRET:=dex-config}"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT HUP INT TERM

$KUBECTL -n "$DEX_NAMESPACE" get secret "$DEX_SECRET" -o jsonpath='{.data.config\.yaml}' | base64 -d >"$workdir/config.yaml"
CONFIG_PATH="$workdir/config.yaml" python3 - <<'PY'
import os
from pathlib import Path
import yaml

path = Path(os.environ["CONFIG_PATH"])
config = yaml.safe_load(path.read_text())
desired = {
    "re8ch-cloud-platform-tool": "RE8CH Cloud Platform Tool",
    "re8ch-cluster-infra-tool": "RE8CH Cluster Infra Tool",
    "re8ch-agent-orchestra-collab": "RE8CH Agent Orchestra Collab",
    "re8ch-tenant-admin": "RE8CH Tenant Admin",
    "re8ch-tenant-registry": "RE8CH Tenant Registry",
    "re8ch-tenant-observability": "RE8CH Tenant Observability",
    "re8ch-tenant-byoc": "RE8CH Tenant BYOC",
}
clients = config.setdefault("staticClients", [])
by_id = {item.get("id"): item for item in clients}
for client_id, name in desired.items():
    client = by_id.get(client_id)
    if client is None:
        client = {"id": client_id}
        clients.append(client)
    client.update({"name": name, "public": True})
    client.pop("secret", None)
    client.pop("redirectURIs", None)
installer = by_id.get("re8ch-marketplace-installer")
if installer is None:
    installer = {"id": "re8ch-marketplace-installer"}
    clients.append(installer)
installer.update({
    "name": "RE8CH Marketplace Installer",
    "public": True,
    "redirectURIs": ["https://marketplace.re8ch.com/install/callback"],
})
installer.pop("secret", None)
path.write_text(yaml.safe_dump(config, sort_keys=False))
PY

backup="${DEX_SECRET}-pre-ai-operations-v2"
$KUBECTL -n "$DEX_NAMESPACE" get secret "$DEX_SECRET" -o json | jq --arg name "$backup" '
  .metadata={name:$name,namespace:.metadata.namespace} |
  del(.metadata.creationTimestamp,.metadata.resourceVersion,.metadata.uid,.metadata.managedFields,.metadata.ownerReferences)' |
  $KUBECTL apply -f - >/dev/null
$KUBECTL -n "$DEX_NAMESPACE" create secret generic "$DEX_SECRET" --from-file=config.yaml="$workdir/config.yaml" --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
$KUBECTL -n "$DEX_NAMESPACE" rollout restart deployment/dex
$KUBECTL -n "$DEX_NAMESPACE" rollout status deployment/dex --timeout=180s
echo "Registered isolated admin, registry, observability and BYOC Dex clients; rollback Secret: $DEX_NAMESPACE/$backup"
