#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="${ROOT_DIR}/manifests/k3s/registry-authority/node-installer.yaml"

kubectl get --raw=/readyz >/dev/null
kubectl apply -f "${MANIFEST}"

if (($#)); then
  nodes=("$@")
else
  nodes=()
  while IFS= read -r node; do
    nodes+=("${node}")
  done < <(kubectl get nodes -o json | jq -r \
    '.items[] | select(any(.status.conditions[]; .type=="Ready" and .status=="True")) | .metadata.name')
fi

for node in "${nodes[@]}"; do
  if [[ "${node}" == restricted-node-6dc3f140f9 ]]; then
    leader_addresses="$(kubectl -n pigsty-external get endpointslice pg-re8ch-primary \
      -o jsonpath='{.endpoints[*].addresses[*]}')"
    if ! grep -qx '192.0.2.183' <<<"${leader_addresses}"; then
      printf 'skip restricted-node-6dc3f140f9: Patroni authority does not identify restricted-node-6dc3f140f9 as leader\n' >&2
      continue
    fi
  fi

  job="registry-authority-${node//[^a-z0-9-]/-}"
  was_unschedulable="$(kubectl get node "${node}" -o jsonpath='{.spec.unschedulable}')"
  kubectl get --raw=/readyz >/dev/null
  kubectl cordon "${node}"
  kubectl -n registry-authority delete job "${job}" --ignore-not-found --wait=true --timeout=90s >/dev/null
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
      hostNetwork: true
      dnsPolicy: ClusterFirstWithHostNet
      restartPolicy: Never
      tolerations: [{operator: Exists}]
      containers:
        - name: installer
          # Bootstrap from the image already cached by the 16-node status
          # DaemonSet, so repairing registry routing never depends on Harbor.
          image: docker.1ms.run/library/python:3.12-alpine
          imagePullPolicy: IfNotPresent
          command: [/bin/sh, /config/install.sh]
          env: [{name: RESTART_K3S, value: "true"}]
          securityContext: {privileged: true, runAsUser: 0, runAsGroup: 0}
          volumeMounts:
            - {name: host, mountPath: /host}
            - {name: config, mountPath: /config, readOnly: true}
      volumes:
        - {name: host, hostPath: {path: /, type: Directory}}
        - {name: config, configMap: {name: k3s-registry-authority-installer, defaultMode: 0555}}
YAML
  kubectl -n registry-authority wait --for=condition=complete "job/${job}" --timeout=90s
  sleep 8
  kubectl wait "node/${node}" --for=condition=Ready --timeout=180s
  kubectl get --raw=/readyz >/dev/null
  if [[ "${was_unschedulable}" != "true" ]]; then
    kubectl uncordon "${node}"
  else
    printf 'preserve existing cordon on %s\n' "${node}"
  fi
done
