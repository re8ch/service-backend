#!/usr/bin/env bash
set -euo pipefail

: "${TENANT_IMAGE_REPOSITORY:=registry.re8ch.com/image/re8ch-ai-operations}"
: "${TENANT_IMAGE_TAG:=${CI_COMMIT_SHA:-$(git rev-parse HEAD)}}"
: "${COSIGN_KEY:?COSIGN_KEY must reference the signing key}"

context=clusters/re8ch/components/ai-operations
image="${TENANT_IMAGE_REPOSITORY}:${TENANT_IMAGE_TAG}"
metadata=$(mktemp)
trap 'rm -f "$metadata"' EXIT

docker buildx build --platform linux/amd64 --provenance=mode=max --sbom=true --push --metadata-file "$metadata" -t "$image" "$context"
digest=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["containerimage.digest"])' "$metadata")
immutable="${TENANT_IMAGE_REPOSITORY}@${digest}"
docker sbom --format spdx-json "$immutable" > tenant-infrastructure.spdx.json
cosign sign --yes --key "$COSIGN_KEY" "$immutable"
cosign verify --key "${COSIGN_PUBLIC_KEY:-$COSIGN_KEY}" "$immutable" >/dev/null
printf '%s\n' "$immutable" | tee tenant-infrastructure.image
