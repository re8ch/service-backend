#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
plugin_dir="$repo_root/platform/plugins/headlamp-mihomos-cluster"

cd "$plugin_dir"
npm run tsc >&2
npm run build >&2
kubectl create configmap headlamp-mihomos-cluster-plugin-bundle \
  --namespace headlamp \
  --from-file=main.js=dist/main.js \
  --from-file=package.json=package.json \
  --dry-run=client -o yaml
