#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
chart_dir="$repo_root/clusters/re8ch/charts/adapter"
registry="${ADAPTER_OCI_REGISTRY:-registry.re8ch.com/helm}"
dist_dir="$(mktemp -d)"
trap 'rm -rf "$dist_dir"' EXIT

chart_version="$(helm show chart "$chart_dir" | sed -n 's/^version: //p')"
if [[ -n "${CI_COMMIT_TAG:-}" && "$CI_COMMIT_TAG" != "adapter-v${chart_version}" ]]; then
  echo "tag $CI_COMMIT_TAG does not match adapter chart version $chart_version" >&2
  exit 1
fi

helm lint "$chart_dir"
package="$(helm package "$chart_dir" --destination "$dist_dir" | sed -n 's/^Successfully packaged chart and saved it to: //p')"
test -n "$package"

if [[ -n "${ADAPTER_REGISTRY_USERNAME:-}" || -n "${ADAPTER_REGISTRY_PASSWORD:-}" ]]; then
  test -n "${ADAPTER_REGISTRY_USERNAME:-}" && test -n "${ADAPTER_REGISTRY_PASSWORD:-}"
  printf '%s' "$ADAPTER_REGISTRY_PASSWORD" | helm registry login "${registry%%/*}" \
    --username "$ADAPTER_REGISTRY_USERNAME" --password-stdin
fi

helm push "$package" "oci://$registry"

if [[ -n "${ADAPTER_ARTIFACTHUB_METADATA_FILE:-}" ]]; then
  test -s "$ADAPTER_ARTIFACTHUB_METADATA_FILE"
  command -v oras >/dev/null
  oras push "${registry}/adapter:artifacthub.io" \
    --config /dev/null:application/vnd.cncf.artifacthub.config.v1+yaml \
    "${ADAPTER_ARTIFACTHUB_METADATA_FILE}:application/vnd.cncf.artifacthub.repository-metadata.layer.v1.yaml"
fi
