#!/usr/bin/env sh
set -eu

: "${KUBECTL:=kubectl}"
: "${NAMESPACE:=egress-fabric}"
: "${GITHUB_APP_ID:?set GITHUB_APP_ID}"
: "${GITHUB_INSTALLATION_ID:?set GITHUB_INSTALLATION_ID}"
: "${GITHUB_PRIVATE_KEY_FILE:?set GITHUB_PRIVATE_KEY_FILE}"

test -s "$GITHUB_PRIVATE_KEY_FILE"
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT HUP INT TERM
umask 077
printf '%s' "$GITHUB_APP_ID" >"$workdir/app-id"
printf '%s' "$GITHUB_INSTALLATION_ID" >"$workdir/installation-id"
cp "$GITHUB_PRIVATE_KEY_FILE" "$workdir/private-key.pem"

$KUBECTL -n "$NAMESPACE" create secret generic access-github-app \
  --from-file=app-id="$workdir/app-id" \
  --from-file=installation-id="$workdir/installation-id" \
  --from-file=private-key.pem="$workdir/private-key.pem" \
  --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null

echo "GitHub App runtime Secret is installed in $NAMESPACE (values were not printed)."
