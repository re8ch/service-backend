#!/usr/bin/env bash
# Sync NPM legacy TLS material into Kubernetes secrets before Traefik takes :443.
set -euo pipefail

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

KUBECTL="${KUBECTL:-sudo k3s kubectl}"

fetch_npm_cert() {
  local node="$1" cert_id="$2" out="$3"
  ssh -o BatchMode=yes "$node" "sudo docker exec nginx-proxy-manager-app-1 cat /etc/letsencrypt/live/${cert_id}/fullchain.pem" >"$TMP/${out}.crt"
  ssh -o BatchMode=yes "$node" "sudo docker exec nginx-proxy-manager-app-1 cat /etc/letsencrypt/live/${cert_id}/privkey.pem" >"$TMP/${out}.key"
}

fetch_file_cert() {
  local node="$1" dir="$2" out="$3"
  ssh -o BatchMode=yes "$node" "sudo cat ${dir}/fullchain.pem" >"$TMP/${out}.crt"
  ssh -o BatchMode=yes "$node" "sudo cat ${dir}/privkey.pem" >"$TMP/${out}.key"
}

echo "==> Export certs"
fetch_npm_cert aspire re8ch-wildcard re8ch-wildcard
fetch_npm_cert qianwen-test api.jz.ynzs.com api-jz-ynzs
fetch_npm_cert qianwen-test nuc-2.ynzs.com nuc-2-ynzs
fetch_npm_cert qianwen-test npm-1 test-api-jz-ynzs
fetch_npm_cert qianwen-test mgr.jz.ynzs.com test-mgr-jz-ynzs
fetch_npm_cert aspire animationcomposer.ynzs.com animationcomposer-ynzs
fetch_file_cert artchais /root/re8ch-api-edge-nginx/certs re8ch-wildcard-artchais

create_tls_secret() {
  local ns="$1" name="$2" cert="$3"
  $KUBECTL -n "$ns" delete secret "$name" --ignore-not-found
  $KUBECTL -n "$ns" create secret tls "$name" --cert="$TMP/${cert}.crt" --key="$TMP/${cert}.key"
}

echo "==> Create kube-system TLS secrets (Traefik default + mounts)"
create_tls_secret kube-system re8ch-wildcard-tls re8ch-wildcard
create_tls_secret kube-system api-jz-ynzs-tls api-jz-ynzs
create_tls_secret kube-system nuc-2-ynzs-tls nuc-2-ynzs
create_tls_secret kube-system test-api-jz-ynzs-tls test-api-jz-ynzs
create_tls_secret kube-system test-mgr-jz-ynzs-tls test-mgr-jz-ynzs
create_tls_secret kube-system animationcomposer-ynzs-tls animationcomposer-ynzs

echo "==> Mirror TLS secrets into qwen for IngressRoute/Ingress references"
create_tls_secret qwen api-jz-ynzs-tls api-jz-ynzs
create_tls_secret qwen nuc-2-ynzs-tls nuc-2-ynzs
create_tls_secret qwen test-api-jz-ynzs-tls test-api-jz-ynzs
create_tls_secret qwen test-mgr-jz-ynzs-tls test-mgr-jz-ynzs
create_tls_secret qwen animationcomposer-ynzs-tls animationcomposer-ynzs

echo "==> Mirror re8ch wildcard to Nuclio nodes for ops backup"
for node in aspire qianwen-test artchais; do
  scp -q "$TMP/re8ch-wildcard.crt" "$TMP/re8ch-wildcard.key" "$node:/tmp/"
  ssh -o BatchMode=yes "$node" "sudo mkdir -p /etc/re8ch/tls/re8ch-wildcard && sudo mv /tmp/re8ch-wildcard.crt /etc/re8ch/tls/re8ch-wildcard/tls.crt && sudo mv /tmp/re8ch-wildcard.key /etc/re8ch/tls/re8ch-wildcard/tls.key && sudo chmod 600 /etc/re8ch/tls/re8ch-wildcard/tls.key"
done

echo "Done."
