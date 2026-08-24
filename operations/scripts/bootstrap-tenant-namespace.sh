#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Bootstrap a tenant K3S namespace with quota, runtime service accounts, and namespace-scoped kubectl access.

Usage:
  ./operations/scripts/bootstrap-tenant-namespace.sh <tenant> [product]

Environment:
  KUBECTL_CMD                  default: kubectl
  LAYER                        default: prod
  PORTAINER_TEAM               default: <tenant>-operators
  POD_QUOTA                    default: 60
  SERVICE_QUOTA                default: 60
  CONFIGMAP_QUOTA              default: 100
  SECRET_QUOTA                 default: 100
  PVC_QUOTA                    default: 10
  DEFAULT_CPU_REQUEST          default: 25m
  DEFAULT_MEMORY_REQUEST       default: 64Mi
  SOURCE_PULL_SECRET_NAMESPACE default: qwen
  SHARED_PULL_SECRET           default: harbor-functions-shared-pull
  CREATE_PERMANENT_TOKENS      default: 0
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 1 ]]; then
  usage
  exit $([[ $# -lt 1 ]] && echo 2 || echo 0)
fi

tenant="$1"
product="${2:-$tenant}"
layer="${LAYER:-prod}"
portainer_team="${PORTAINER_TEAM:-${tenant}-operators}"
kubectl_cmd="${KUBECTL_CMD:-kubectl}"

pod_quota="${POD_QUOTA:-60}"
service_quota="${SERVICE_QUOTA:-60}"
configmap_quota="${CONFIGMAP_QUOTA:-100}"
secret_quota="${SECRET_QUOTA:-100}"
pvc_quota="${PVC_QUOTA:-10}"
default_cpu="${DEFAULT_CPU_REQUEST:-25m}"
default_memory="${DEFAULT_MEMORY_REQUEST:-64Mi}"
source_pull_ns="${SOURCE_PULL_SECRET_NAMESPACE:-qwen}"
shared_pull_secret="${SHARED_PULL_SECRET:-harbor-functions-shared-pull}"
create_tokens="${CREATE_PERMANENT_TOKENS:-0}"

if [[ ! "$tenant" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
  echo "Invalid tenant/namespace: $tenant" >&2
  exit 2
fi

kctl() {
  # shellcheck disable=SC2086
  $kubectl_cmd "$@"
}

cat <<YAML | kctl apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: ${tenant}
  labels:
    app.kubernetes.io/part-of: re8ch-saas
    portainer.re8ch.com/team: ${portainer_team}
    saas.re8ch.com/layer: ${layer}
    saas.re8ch.com/product: ${product}
    saas.re8ch.com/tenant: ${tenant}
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: tenant-object-quota
  namespace: ${tenant}
spec:
  hard:
    configmaps: "${configmap_quota}"
    persistentvolumeclaims: "${pvc_quota}"
    pods: "${pod_quota}"
    secrets: "${secret_quota}"
    services: "${service_quota}"
---
apiVersion: v1
kind: LimitRange
metadata:
  name: tenant-defaults
  namespace: ${tenant}
spec:
  limits:
  - type: Container
    defaultRequest:
      cpu: ${default_cpu}
      memory: ${default_memory}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${tenant}-runtime
  namespace: ${tenant}
  labels:
    saas.re8ch.com/tenant: ${tenant}
imagePullSecrets:
- name: ${shared_pull_secret}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${tenant}-kubectl-operator
  namespace: ${tenant}
  labels:
    saas.re8ch.com/access: kubectl
    saas.re8ch.com/tenant: ${tenant}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${tenant}-kubectl-viewer
  namespace: ${tenant}
  labels:
    saas.re8ch.com/access: kubectl
    saas.re8ch.com/tenant: ${tenant}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tenant-operator
  namespace: ${tenant}
rules:
- apiGroups: ["", "apps", "batch", "autoscaling", "networking.k8s.io", "discovery.k8s.io", "jobset.x-k8s.io", "nuclio.io"]
  resources: ["*"]
  verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tenant-viewer
  namespace: ${tenant}
rules:
- apiGroups: ["", "apps", "batch", "autoscaling", "networking.k8s.io", "discovery.k8s.io", "jobset.x-k8s.io", "nuclio.io"]
  resources: ["*"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${tenant}-kubectl-operator
  namespace: ${tenant}
subjects:
- kind: ServiceAccount
  name: ${tenant}-kubectl-operator
  namespace: ${tenant}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tenant-operator
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${tenant}-kubectl-viewer
  namespace: ${tenant}
subjects:
- kind: ServiceAccount
  name: ${tenant}-kubectl-viewer
  namespace: ${tenant}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tenant-viewer
YAML

if kctl -n "$source_pull_ns" get secret "$shared_pull_secret" >/dev/null 2>&1; then
  kctl -n "$source_pull_ns" get secret "$shared_pull_secret" -o json \
    | TENANT_NS="$tenant" jq 'del(.metadata.uid,.metadata.resourceVersion,.metadata.creationTimestamp,.metadata.managedFields,.metadata.annotations["kubectl.kubernetes.io/last-applied-configuration"]) | .metadata.namespace=env.TENANT_NS' \
    | kctl apply -f -
  kctl -n "$tenant" patch serviceaccount default --type merge \
    -p "{\"imagePullSecrets\":[{\"name\":\"${shared_pull_secret}\"}]}" >/dev/null
else
  echo "warning: shared pull secret ${source_pull_ns}/${shared_pull_secret} not found" >&2
fi

if [[ "$create_tokens" == "1" ]]; then
  for sa in "${tenant}-kubectl-operator" "${tenant}-kubectl-viewer"; do
    cat <<YAML | kctl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: ${sa}-permanent-token
  namespace: ${tenant}
  annotations:
    kubernetes.io/service-account.name: ${sa}
  labels:
    saas.re8ch.com/access: kubectl
    saas.re8ch.com/tenant: ${tenant}
type: kubernetes.io/service-account-token
YAML
  done
fi

kctl -n "$tenant" get resourcequota,limitrange,sa,role,rolebinding
kctl auth can-i create deployments -n "$tenant" --as="system:serviceaccount:${tenant}:${tenant}-kubectl-operator"
kctl auth can-i patch deployments -n "$tenant" --as="system:serviceaccount:${tenant}:${tenant}-kubectl-viewer"
