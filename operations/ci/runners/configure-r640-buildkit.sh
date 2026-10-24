#!/usr/bin/env bash
set -euo pipefail

# Run as github-runner on restricted-node-6dc3f140f9 after loading the checked-in runner .env.
# Recreating the builder intentionally discards its local build cache.

builder_name="${BUILDX_BUILDER:-r640-mihomos}"
proxy_url="${R640_BUILD_PROXY_URL:-http://127.0.0.1:17891}"

if [[ "$(id -un)" != "github-runner" ]]; then
  echo "run this script as github-runner" >&2
  exit 1
fi
if [[ "${proxy_url}" != "http://127.0.0.1:17891" ]]; then
  echo "restricted-node-6dc3f140f9 BuildKit must use the declared local Mihomos endpoint" >&2
  exit 1
fi

if docker buildx inspect "${builder_name}" >/dev/null 2>&1; then
  docker buildx rm "${builder_name}"
fi

docker buildx create \
  --name "${builder_name}" \
  --driver docker-container \
  --driver-opt network=host \
  --driver-opt "env.http_proxy=${proxy_url}" \
  --driver-opt "env.https_proxy=${proxy_url}" \
  --driver-opt "env.HTTP_PROXY=${proxy_url}" \
  --driver-opt "env.HTTPS_PROXY=${proxy_url}" \
  --use \
  --bootstrap

docker buildx inspect "${builder_name}"

