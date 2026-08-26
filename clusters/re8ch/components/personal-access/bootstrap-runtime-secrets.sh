#!/usr/bin/env sh
set -eu

: "${KUBECTL:=kubectl}"
: "${NAMESPACE:=egress-fabric}"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT HUP INT TERM
umask 077

if ! $KUBECTL -n "$NAMESPACE" get secret access-subscription-secrets >/dev/null 2>&1; then
  openssl rand -hex 32 >"$workdir/hmac-key"
  $KUBECTL -n "$NAMESPACE" create secret generic access-subscription-secrets \
    --from-file=hmac-key="$workdir/hmac-key" --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
fi

if ! $KUBECTL -n "$NAMESPACE" get secret access-controller-api >/dev/null 2>&1; then
  openssl rand -hex 32 >"$workdir/controller-secret"
  $KUBECTL -n "$NAMESPACE" create secret generic access-controller-api \
    --from-file=controller-secret="$workdir/controller-secret" --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
fi

if ! $KUBECTL -n "$NAMESPACE" get secret personal-access-users >/dev/null 2>&1; then
  printf '          disabled-bootstrap: disabled\n' >"$workdir/users.yaml"
  $KUBECTL -n "$NAMESPACE" create secret generic personal-access-users \
    --from-file=users.yaml="$workdir/users.yaml" --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
fi

if ! $KUBECTL -n "$NAMESPACE" get configmap access-subscription-devices >/dev/null 2>&1; then
  $KUBECTL -n "$NAMESPACE" create configmap access-subscription-devices \
    --from-literal=devices.json='{}' --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
fi

if ! $KUBECTL -n "$NAMESPACE" get configmap access-members-cache >/dev/null 2>&1; then
  $KUBECTL -n "$NAMESPACE" create configmap access-members-cache \
    --from-literal=members.json='{"repository":"re8ch/access-edge-members","lastSuccess":0,"members":{}}' \
    --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
fi

if ! $KUBECTL -n "$NAMESPACE" get configmap access-device-usage >/dev/null 2>&1; then
  $KUBECTL -n "$NAMESPACE" create configmap access-device-usage \
    --from-literal=initialized=true --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
fi

echo "Runtime Secrets and device state are present in $NAMESPACE (values were not printed)."
