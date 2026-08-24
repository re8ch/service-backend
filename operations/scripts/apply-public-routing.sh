#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
context="${KUBE_CONTEXT:-$(kubectl config current-context)}"
regional="$repo_root/clusters/re8ch/components/regional-routing"

python3 "$repo_root/operations/scripts/validate-public-routing.py"

# CRDs must be discoverable before their instances are decoded by kubectl.
kubectl --context "$context" apply -f "$regional/public-edge-crd.yaml"
kubectl --context "$context" wait --for=condition=Established crd/publicedges.networking.re8ch.com --timeout=60s
kubectl --context "$context" apply -f "$regional/public-edge-rbac.yaml"
kubectl --context "$context" apply --server-side --field-manager=re8ch-public-routing -f "$regional/node-edge-labels.yaml"

# The CRD file also contains the initial PublicEdge resources.
kubectl --context "$context" apply -f "$regional/public-edge-crd.yaml"

if ! kubectl --context "$context" api-resources --api-group=cert-manager.io | grep -q '^certificates'; then
  kubectl --context "$context" apply -f "$regional/cert-manager-bootstrap.yaml"
  kubectl --context "$context" wait --for=condition=Available deployment/cert-manager-webhook -n cert-manager --timeout=300s
fi

# Re-apply after CRD discovery so ClusterIssuer is created, then wait before certificates.
kubectl --context "$context" apply -f "$regional/cert-manager-bootstrap.yaml"
kubectl --context "$context" wait --for=condition=Complete job/cert-manager-cloudflare-bootstrap -n cert-manager --timeout=180s
kubectl --context "$context" apply -f "$regional/clusterissuer.yaml"
kubectl --context "$context" wait --for=condition=Ready clusterissuer/re8ch-cloudflare-dns01 --timeout=180s
kubectl --context "$context" apply -f "$regional/gateway-certificates.yaml"
kubectl --context "$context" wait --for=condition=Ready certificate/re8ch-public-gateway -n kube-system --timeout=300s
kubectl --context "$context" wait --for=condition=Ready certificate/re8ch-tools-public-gateway -n kube-system --timeout=300s

# Render with the repository's intentional cross-directory authority.py generator.
kubectl kustomize "$regional" --load-restrictor=LoadRestrictionsNone \
  | kubectl --context "$context" apply -f -

python3 "$repo_root/operations/scripts/validate-public-routing.py" --live-context "$context"
kubectl --context "$context" get publicedge,gateway,httproute,tlsroute -A
