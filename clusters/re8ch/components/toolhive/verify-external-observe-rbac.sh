#!/usr/bin/env sh
set -eu

: "${KUBECTL:=kubectl}"
subject=system:serviceaccount:toolhive-system:ops-mcp-public

allow() {
  if [ -n "${3:-}" ]; then
    answer=$($KUBECTL auth can-i "$1" "$2" -n "$3" --as="$subject")
  else
    answer=$($KUBECTL auth can-i "$1" "$2" --as="$subject")
  fi
  [ "$answer" = yes ] || { echo "FAIL expected allow: $1 $2 ${3:-cluster}" >&2; exit 1; }
}

deny() {
  if [ -n "${3:-}" ]; then
    answer=$($KUBECTL auth can-i "$1" "$2" -n "$3" --as="$subject" || true)
  else
    answer=$($KUBECTL auth can-i "$1" "$2" --as="$subject" || true)
  fi
  [ "$answer" = no ] || { echo "FAIL expected deny: $1 $2 ${3:-cluster}" >&2; exit 1; }
}

deny_subresource() {
  answer=$($KUBECTL auth can-i "$1" "$2" --subresource="$3" -n "$4" --as="$subject" || true)
  [ "$answer" = no ] || { echo "FAIL expected deny: $1 $2/$3 $4" >&2; exit 1; }
}

allow list namespaces
allow list deployments.apps auth
allow list deployments.apps supabase-pg-re8ch
allow list deployments.apps harbor
allow get mcpservers.toolhive.stacklok.dev/ops-observe-external toolhive-system
allow list roles.rbac.authorization.k8s.io auth
allow list rolebindings.rbac.authorization.k8s.io auth
allow list serviceaccounts auth
deny list secrets auth
deny_subresource get pods log auth
deny_subresource create pods exec auth
deny patch deployments.apps/authentik-server auth
deny list clusterroles.rbac.authorization.k8s.io
echo "external observe RBAC verified"
