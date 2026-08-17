#!/usr/bin/env bash
set -euo pipefail

KUBECTL=${KUBECTL:-"sudo k3s kubectl"}
namespace=${NAMESPACE:-ai-operations}
secret_name=${SECRET_NAME:-terminal-provider-blaxel}
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT INT TERM
chmod 700 "$tmp_dir"

read -r -p 'Blaxel workspace: ' workspace
read -r -s -p 'Blaxel API key: ' api_key
printf '\n'
read -r -p 'Existing Blaxel volume name: ' volume_name
printf '%s' "$workspace" >"$tmp_dir/BL_WORKSPACE"
printf '%s' "$api_key" >"$tmp_dir/BL_API_KEY"
printf '%s' "$volume_name" >"$tmp_dir/BLAXEL_VOLUME_NAME"
chmod 600 "$tmp_dir"/*
unset api_key

# shellcheck disable=SC2086
$KUBECTL -n "$namespace" create secret generic "$secret_name" \
  --from-file=BL_WORKSPACE="$tmp_dir/BL_WORKSPACE" \
  --from-file=BL_API_KEY="$tmp_dir/BL_API_KEY" \
  --from-file=BLAXEL_VOLUME_NAME="$tmp_dir/BLAXEL_VOLUME_NAME" \
  --dry-run=client -o yaml | $KUBECTL apply -f - >/dev/null
echo "registered $namespace/$secret_name (values not printed)"
