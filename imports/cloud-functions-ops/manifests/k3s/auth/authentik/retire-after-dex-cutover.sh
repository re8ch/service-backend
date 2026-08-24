#!/usr/bin/env sh
set -eu

# Authentik is retained at zero replicas as a rollback data set only. Dex is
# the sole live OAuth/OIDC issuer for ToolHive and Codex.
: "${KUBECTL:=kubectl}"

for deployment in authentik-server authentik-worker authentik-redis; do
  $KUBECTL -n auth scale "deployment/$deployment" --replicas=0
  $KUBECTL -n auth annotate "deployment/$deployment" \
    re8ch.com/retired-by=dex-single-layer-cutover --overwrite >/dev/null
done

echo "Authentik workloads retired at zero replicas; Secrets and PVCs retained for rollback."
