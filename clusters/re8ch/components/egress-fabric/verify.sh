#!/usr/bin/env bash
set -euo pipefail
: "${KUBECTL:=sudo k3s kubectl}"
${KUBECTL} get --raw=/readyz
${KUBECTL} get nodes
${KUBECTL} -n egress-fabric rollout status deploy/mihomo-egress-la --timeout=5m
${KUBECTL} -n egress-fabric rollout status ds/mihomo-node --timeout=10m
${KUBECTL} -n egress-fabric rollout status deploy/path-controller --timeout=5m
${KUBECTL} -n egress-fabric get pods,svc -o wide
${KUBECTL} -n egress-fabric logs deploy/path-controller --tail=5
${KUBECTL} -n egress-fabric run route-controller-smoke --rm -i --restart=Never \
  --image=curlimages/curl:8.15.0 -- \
  curl --fail --silent http://path-controller:8080/healthz
