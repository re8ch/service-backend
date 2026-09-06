#!/usr/bin/env bash
set -euo pipefail

# This publisher needs the Macmini shell runner, not the validation container.
for tool in docker python3 cosign; do
  command -v "$tool" >/dev/null || {
    echo "Missing $tool; use the macmini-service-backend-shell runner" >&2
    exit 127
  }
done
docker info >/dev/null
docker buildx version >/dev/null
docker sbom version >/dev/null
if [[ "${1:-}" == "--check" ]]; then
  echo "Tenant release tools and Docker daemon are available"
  exit 0
fi

: "${TENANT_IMAGE_REPOSITORY:=registry.re8ch.com/image/re8ch-ai-operations}"
: "${TENANT_IMAGE_TAG:=${CI_COMMIT_SHA:-$(git rev-parse HEAD)}}"
dockerfile=clusters/re8ch/components/ai-operations/Dockerfile
image="${TENANT_IMAGE_REPOSITORY}:${TENANT_IMAGE_TAG}"
metadata=$(mktemp)
trap 'rm -f "$metadata"' EXIT

: "${HARBOR_USERNAME:?HARBOR_USERNAME is required for registry publication}"
: "${HARBOR_PASSWORD:?HARBOR_PASSWORD is required for registry publication}"
printf '%s' "$HARBOR_PASSWORD" | docker login "${TENANT_IMAGE_REPOSITORY%%/*}" --username "$HARBOR_USERNAME" --password-stdin >/dev/null

sbom_generator="${BUILDKIT_SBOM_GENERATOR:-docker.1ms.run/docker/buildkit-syft-scanner:stable-1}"
push_attempt=1
push_attempts="${TENANT_IMAGE_PUSH_ATTEMPTS:-3}"
while ! docker buildx build --platform linux/amd64 --provenance=mode=max \
  --attest "type=sbom,generator=${sbom_generator}" \
  --push --metadata-file "$metadata" -f "$dockerfile" -t "$image" .; do
  if (( push_attempt >= push_attempts )); then
    echo "Image build/push failed after ${push_attempt} attempts" >&2
    exit 1
  fi
  delay=$((push_attempt * 10))
  echo "Image build/push attempt ${push_attempt}/${push_attempts} failed; retrying with a fresh registry upload session in ${delay}s" >&2
  sleep "$delay"
  push_attempt=$((push_attempt + 1))
done
digest=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["containerimage.digest"])' "$metadata")
immutable="${TENANT_IMAGE_REPOSITORY}@${digest}"
docker sbom --platform linux/amd64 --format spdx-json "$immutable" > tenant-infrastructure.spdx.json
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
