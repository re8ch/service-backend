#!/usr/bin/env bash
set -euo pipefail

: "${TENANT_IMAGE_REPOSITORY:=registry.re8ch.com/image/re8ch-ai-operations}"
: "${TENANT_IMAGE_TAG:=${CI_COMMIT_SHA:-$(git rev-parse HEAD)}}"
context=clusters/re8ch/components/ai-operations
image="${TENANT_IMAGE_REPOSITORY}:${TENANT_IMAGE_TAG}"
metadata=$(mktemp)
trap 'rm -f "$metadata"' EXIT

sbom_generator="${BUILDKIT_SBOM_GENERATOR:-docker.1ms.run/docker/buildkit-syft-scanner:stable-1}"
docker buildx build --platform linux/amd64 --provenance=mode=max \
  --attest "type=sbom,generator=${sbom_generator}" \
  --push --metadata-file "$metadata" -t "$image" "$context"
digest=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["containerimage.digest"])' "$metadata")
immutable="${TENANT_IMAGE_REPOSITORY}@${digest}"
docker sbom --format spdx-json "$immutable" > tenant-infrastructure.spdx.json
if [[ -n "${COSIGN_KEY:-}" ]]; then
  cosign sign --yes --key "$COSIGN_KEY" "$immutable"
  cosign verify --key "${COSIGN_PUBLIC_KEY:-$COSIGN_KEY}" "$immutable" >/dev/null
elif [[ -n "${SIGSTORE_ID_TOKEN:-}" ]]; then
  cosign sign --yes --identity-token "$SIGSTORE_ID_TOKEN" "$immutable"
  cosign verify \
    --certificate-identity "${CI_PROJECT_URL}//.gitlab-ci.yml@refs/heads/${CI_COMMIT_REF_NAME}" \
    --certificate-oidc-issuer "$CI_SERVER_URL" \
    "$immutable" >/dev/null
else
  echo "COSIGN_KEY or GitLab SIGSTORE_ID_TOKEN is required" >&2
  exit 1
fi
printf '%s\n' "$immutable" | tee tenant-infrastructure.image
