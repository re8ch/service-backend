#!/usr/bin/env bash
set -euo pipefail

KUBECTL=${KUBECTL:-kubectl}

test "$($KUBECTL get nodes --no-headers | awk '$2 ~ /^Ready/ {n++} END {print n+0}')" -eq 14
test "$($KUBECTL -n kube-system get pods -l k8s-app=cilium --no-headers | awk '$2=="1/1" && $3=="Running" {n++} END {print n+0}')" -eq 14

for _ in 1 2 3 4; do
  test "$($KUBECTL get --raw /apis/metrics.k8s.io/v1beta1/nodes | jq '.items | length')" -eq 14
done

echo "nodes=14 cilium=14 metrics=14/14 stable"
