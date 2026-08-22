#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
kubectl apply -f "${ROOT_DIR}/manifests/k3s/registry-authority/node-installer.yaml"
kubectl apply -f "${ROOT_DIR}/manifests/k3s/node-dns/node-installer.yaml"
if (($#)); then nodes=("$@"); else
  nodes=()
  while IFS= read -r node; do nodes+=("${node}"); done < <(
    kubectl get nodes -o json | jq -r '.items[]|select(any(.status.conditions[];.type=="Ready" and .status=="True"))|.metadata.name')
fi
for node in "${nodes[@]}"; do
  job="node-dns-${node//[^a-z0-9-]/-}"
  kubectl -n registry-authority delete job "${job}" --ignore-not-found --wait=false >/dev/null
  kubectl apply -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata: {name: ${job}, namespace: registry-authority}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 300
  template:
    spec:
      nodeName: ${node}
      hostPID: true
      restartPolicy: Never
      tolerations: [{operator: Exists}]
      containers:
        - name: installer
          image: registry.re8ch.com/proxy-dockerhub/library/alpine:3.22
          command: [/bin/sh, /config/install.sh]
          securityContext: {privileged: true}
          volumeMounts: [{name: host, mountPath: /host}, {name: config, mountPath: /config, readOnly: true}]
      volumes:
        - {name: host, hostPath: {path: /, type: Directory}}
        - {name: config, configMap: {name: node-dns-installer, defaultMode: 0555}}
YAML
  kubectl -n registry-authority wait --for=condition=complete "job/${job}" --timeout=90s
done
