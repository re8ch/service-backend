#!/usr/bin/env bash
set -euo pipefail

chart_version="${TRAEFIK_CRDS_CHART_VERSION:-1.18.0}"

helm repo add traefik https://traefik.github.io/charts --force-update >/dev/null
helm repo update traefik >/dev/null
helm template re8ch-traefik-crds traefik/traefik-crds \
  --version "${chart_version}" \
  --set traefik=true \
  --set gatewayAPI=false \
  --set gatewayAPIExperimental=false \
  --set hub=true \
  --set knative=false \
  | kubectl apply --server-side --field-manager=re8ch-traefik-crds -f -

# K3s 1.36.0-rc3 bundles Gateway API v1.4 in this subchart. The cluster's
# v1.6 safe-upgrade policy correctly rejects that downgrade, so CRDs are
# managed above while the main Traefik HelmChart remains K3s-owned.
kubectl delete helmchart.helm.cattle.io/traefik-crd \
  --namespace kube-system \
  --ignore-not-found
