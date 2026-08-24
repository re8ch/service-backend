#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
KUBECTL=${KUBECTL:-kubectl}
NAMESPACE=${NAMESPACE:-qianwen-ops}
OPENCOST_CHART_VERSION=${OPENCOST_CHART_VERSION:-2.5.28}

cd "$ROOT_DIR"

$KUBECTL -n "$NAMESPACE" create configmap re8ch-network-costs \
  --from-file=network-costs.yaml=clusters/re8ch/components/network/public-mesh/network-costs.yaml \
  --dry-run=client -o yaml | $KUBECTL apply -f -
$KUBECTL -n "$NAMESPACE" create configmap re8ch-node-costs \
  --from-file=node-costs.yaml=clusters/re8ch/components/network/public-mesh/node-costs.yaml \
  --dry-run=client -o yaml | $KUBECTL apply -f -
$KUBECTL -n "$NAMESPACE" create configmap re8ch-routing-policy \
  --from-file=routing-policy.yaml=clusters/re8ch/components/network/public-mesh/routing-policy.yaml \
  --dry-run=client -o yaml | $KUBECTL apply -f -
$KUBECTL -n "$NAMESPACE" create configmap re8ch-routing-advisor-script \
  --from-file=re8ch-routing-advisor.py=operations/scripts/re8ch-routing-advisor.py \
  --dry-run=client -o yaml | $KUBECTL apply -f -
$KUBECTL apply -f clusters/re8ch/components/network/public-mesh/network-economics.yaml
$KUBECTL -n "$NAMESPACE" rollout status deployment/re8ch-routing-advisor --timeout=180s

$KUBECTL create namespace opencost --dry-run=client -o yaml | $KUBECTL apply -f -
helm template opencost opencost/opencost \
  --version "$OPENCOST_CHART_VERSION" \
  --namespace opencost \
  -f clusters/re8ch/components/network/public-mesh/opencost-values.yaml | $KUBECTL apply -f -
$KUBECTL -n opencost rollout status deployment/opencost --timeout=240s

LOCAL_SOURCE_DIR=clusters/re8ch/components/qianwen-ops/grafana-dashboards/qianwen \
  ./operations/scripts/apply-re8ch-grafana-dashboards.sh
