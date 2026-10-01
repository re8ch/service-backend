#!/usr/bin/env bash
set -euo pipefail

# This publisher needs the Macmini shell runner, not the validation container.
for tool in docker python3 cosign; do
  command -v "$tool" >/dev/null || {
    echo "Missing $tool; use the macmini-service-backend-shell runner" >&2
    exit 127
  }
done
echo "Checking Docker daemon and Buildx"
docker info >/dev/null
docker buildx version >/dev/null
if ! docker sbom version >/dev/null 2>&1 && ! command -v syft >/dev/null; then
  echo "Missing docker sbom or syft" >&2
  exit 127
fi

# The Macmini runner may keep a remote restricted-node-6dc3f140f9 builder selected even while its
# local tunnel is down. Release correctness does not depend on that builder:
# select the known local BuildKit instance unless the caller explicitly pins a
# healthy alternative.
: "${TENANT_IMAGE_BUILDER:=re8ch-direct}"
run_with_release_timeout() {
  local duration="$1"
  shift
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    timeout --signal=TERM --kill-after=30s "$duration" "$@"
  else
    "$@"
  fi
}
echo "Checking Buildx builder ${TENANT_IMAGE_BUILDER}"
if ! run_with_release_timeout 90s docker buildx inspect --bootstrap "$TENANT_IMAGE_BUILDER" >/dev/null 2>&1; then
  if run_with_release_timeout 90s docker buildx inspect --bootstrap default >/dev/null 2>&1; then
    TENANT_IMAGE_BUILDER=default
  else
    echo "No healthy local BuildKit builder is available" >&2
    exit 1
  fi
fi
if [[ "${1:-}" == "--check" ]]; then
  echo "Tenant release tools, Docker daemon and builder ${TENANT_IMAGE_BUILDER} are available"
  exit 0
fi

: "${TENANT_IMAGE_REPOSITORY:=registry.re8ch.com/image/re8ch-ai-operations}"
: "${TENANT_IMAGE_TAG:=${GITHUB_SHA:-$(git rev-parse HEAD)}}"
: "${TENANT_IMAGE_DOCKERFILE:=clusters/re8ch/components/ai-operations/Dockerfile}"
: "${TENANT_IMAGE_ARTIFACT_NAME:=tenant-infrastructure}"
dockerfile="$TENANT_IMAGE_DOCKERFILE"
image="${TENANT_IMAGE_REPOSITORY}:${TENANT_IMAGE_TAG}"
metadata=$(mktemp)
trap 'rm -f "$metadata"' EXIT

: "${HARBOR_USERNAME:?HARBOR_USERNAME is required for registry publication}"
: "${HARBOR_PASSWORD:?HARBOR_PASSWORD is required for registry publication}"
echo "Configuring image registry authentication for ${TENANT_IMAGE_REPOSITORY%%/*}"
if [[ "${TENANT_IMAGE_INLINE_AUTH:-}" == "1" ]]; then
  : "${DOCKER_CONFIG:?DOCKER_CONFIG is required for inline release auth}"
  python3 - "$DOCKER_CONFIG/config.json" "${TENANT_IMAGE_REPOSITORY%%/*}" <<'PY'
import base64
import json
import os
import sys

path, registry = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    config = json.load(stream)
credential = f"{os.environ['HARBOR_USERNAME']}:{os.environ['HARBOR_PASSWORD']}".encode()
config.setdefault("auths", {})[registry] = {
    "auth": base64.b64encode(credential).decode("ascii")
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(config, stream)
os.chmod(path, 0o600)
PY
else
  printf '%s' "$HARBOR_PASSWORD" | docker login "${TENANT_IMAGE_REPOSITORY%%/*}" --username "$HARBOR_USERNAME" --password-stdin >/dev/null
fi

sbom_generator="${BUILDKIT_SBOM_GENERATOR:-docker.1ms.run/docker/buildkit-syft-scanner:stable-1}"
push_attempt=1
push_attempts="${TENANT_IMAGE_PUSH_ATTEMPTS:-3}"
while ! run_with_release_timeout 5m docker buildx build --builder "$TENANT_IMAGE_BUILDER" --platform linux/amd64 --provenance=mode=max \
  --attest "type=sbom,generator=${sbom_generator}" \
  --progress plain --push --metadata-file "$metadata" -f "$dockerfile" -t "$image" .; do
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
echo "Image published at ${immutable}; generating SBOM"
if docker sbom version >/dev/null 2>&1; then
  docker sbom --platform linux/amd64 --format spdx-json "$immutable" > "${TENANT_IMAGE_ARTIFACT_NAME}.spdx.json"
else
  syft "$immutable" -o spdx-json > "${TENANT_IMAGE_ARTIFACT_NAME}.spdx.json"
fi
if [[ -n "${COSIGN_KEY:-}" ]]; then
  echo "Signing image with configured key"
  cosign sign --yes --key "$COSIGN_KEY" "$immutable"
  cosign verify --key "${COSIGN_PUBLIC_KEY:-$COSIGN_KEY}" "$immutable" >/dev/null
elif [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  echo "Signing image with GitHub OIDC"
  certificate_identity="${SIGSTORE_CERTIFICATE_IDENTITY:-https://github.com/re8ch/service-backend/.github/workflows/tenant-release.yml@refs/heads/main}"
  cosign sign --yes "$immutable"
  cosign verify \
    --certificate-identity "$certificate_identity" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    "$immutable" >/dev/null
else
  echo "COSIGN_KEY or GitHub Actions OIDC is required" >&2
  exit 1
fi
printf '%s\n' "$immutable" | tee "${TENANT_IMAGE_ARTIFACT_NAME}.image"
