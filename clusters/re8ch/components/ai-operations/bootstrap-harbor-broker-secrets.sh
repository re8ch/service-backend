#!/usr/bin/env sh
set -eu
: "${KUBECTL:=kubectl}"

# Copy credential material entirely through process pipes. No value is printed,
# placed in argv, or written to this repository.
$KUBECTL -n harbor get secret harbor-core-env -o json \
  | jq '{apiVersion:"v1",kind:"Secret",metadata:{name:"cluster-infra-harbor",namespace:"ai-operations"},type:"Opaque",data:{password:.data.HARBOR_ADMIN_PASSWORD},stringData:{username:"admin"}}' \
  | $KUBECTL apply -f - >/dev/null

$KUBECTL -n ai-operations get secret harbor-registry-pull -o json \
  | jq 'del(.metadata.annotations,.metadata.creationTimestamp,.metadata.resourceVersion,.metadata.uid,.metadata.managedFields) | .metadata.name="harbor-registry-pull" | .metadata.namespace="credential-broker"' \
  | $KUBECTL apply -f - >/dev/null

echo "Harbor credential contracts are present; credential values were not displayed."
