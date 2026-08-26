#!/usr/bin/env sh
set -eu

: "${KUBECTL:=kubectl}"
: "${DEX_NAMESPACE:=dex}"
: "${DEX_SECRET:=dex-config}"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT HUP INT TERM
umask 077

$KUBECTL -n "$DEX_NAMESPACE" get secret "$DEX_SECRET" \
  -o jsonpath='{.data.config\.yaml}' | base64 -d >"$workdir/config.yaml"
openssl rand -hex 32 >"$workdir/client-secret"
openssl rand -base64 24 | tr -d '\n' >"$workdir/cookie-secret"
openssl rand -base64 24 | tr -d '\n' >"$workdir/dns-cookie-secret"

CONFIG_PATH="$workdir/config.yaml" CLIENT_SECRET_PATH="$workdir/client-secret" python3 - <<'PY'
import os
from pathlib import Path
import yaml

path = Path(os.environ["CONFIG_PATH"])
secret = Path(os.environ["CLIENT_SECRET_PATH"]).read_text().strip()
config = yaml.safe_load(path.read_text())
clients = config.setdefault("staticClients", [])
client = next((item for item in clients if item.get("id") == "access-subscription"), None)
if client is None:
    client = {"id": "access-subscription"}
    clients.append(client)
client.update({
    "name": "access re8ch com",
    "secret": secret,
    "redirectURIs": [
        "https://sub.access.re8ch.com/oauth2/callback",
        "https://dns.access.re8ch.com/oauth2/callback",
    ],
})
client.pop("public", None)
path.write_text(yaml.safe_dump(config, sort_keys=False))
PY

{
  printf 'OAUTH2_PROXY_CLIENT_ID=access-subscription\nOAUTH2_PROXY_CLIENT_SECRET='
  cat "$workdir/client-secret"
  printf '\nOAUTH2_PROXY_COOKIE_SECRET='
  cat "$workdir/cookie-secret"
  printf '\n'
} >"$workdir/oauth2-proxy.env"

{
  printf 'OAUTH2_PROXY_CLIENT_ID=access-subscription\nOAUTH2_PROXY_CLIENT_SECRET='
  cat "$workdir/client-secret"
  printf '\nOAUTH2_PROXY_COOKIE_SECRET='
  cat "$workdir/dns-cookie-secret"
  printf '\n'
} >"$workdir/oauth2-proxy-dns.env"

backup="${DEX_SECRET}-pre-access-subscription-$(date +%Y%m%d%H%M%S)"
$KUBECTL -n "$DEX_NAMESPACE" get secret "$DEX_SECRET" -o json | \
  jq --arg name "$backup" '.metadata={name:$name,namespace:.metadata.namespace} | del(.metadata.creationTimestamp,.metadata.resourceVersion,.metadata.uid,.metadata.managedFields,.metadata.ownerReferences)' | \
  $KUBECTL apply -f - >/dev/null

$KUBECTL -n "$DEX_NAMESPACE" create secret generic "$DEX_SECRET" \
  --from-file=config.yaml="$workdir/config.yaml" --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
$KUBECTL -n egress-fabric create secret generic oauth2-proxy-access-subscription \
  --from-env-file="$workdir/oauth2-proxy.env" --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
$KUBECTL -n egress-fabric create secret generic oauth2-proxy-access-dns \
  --from-env-file="$workdir/oauth2-proxy-dns.env" --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null

$KUBECTL -n "$DEX_NAMESPACE" rollout restart deployment/dex >/dev/null
$KUBECTL -n "$DEX_NAMESPACE" rollout status deployment/dex --timeout=180s
echo "Registered access-subscription Dex client; rollback Secret: $DEX_NAMESPACE/$backup"
