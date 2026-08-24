#!/usr/bin/env bash
set -euo pipefail

namespace="${1:-qwen}"
pool_patch='{"spec":{"nodeSelector":{"pigsty.cc/workload":null,"node-role.kubernetes.io/nuclio":"true"}}}'
pod_patch='{"spec":{"template":{"spec":{"nodeSelector":{"kubernetes.io/hostname":null,"pigsty.cc/workload":null,"node-role.kubernetes.io/nuclio":"true"},"topologySpreadConstraints":[{"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway","labelSelector":{"matchLabels":{"nuclio.io/class":"function"}}}]}}}}'

kubectl -n "$namespace" get nucliofunctions.nuclio.io -o name | while read -r fn; do
  kubectl -n "$namespace" patch "$fn" --type=merge --patch "$pool_patch"
done
kubectl -n "$namespace" get deploy -l nuclio.io/class=function -o name | while read -r deploy; do
  kubectl -n "$namespace" patch "$deploy" --type=merge --patch "$pod_patch"
done
kubectl -n "$namespace" rollout restart deploy -l nuclio.io/class=function
