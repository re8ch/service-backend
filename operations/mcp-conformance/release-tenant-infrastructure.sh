#!/usr/bin/env bash
set -euo pipefail

# Publication only needs Buildx and Python. Finalization additionally needs the
# signing and SBOM tools, but split release workflows install those later.
: "${TENANT_IMAGE_SKIP_FINALIZE:=0}"
for tool in docker python3; do
  command -v "$tool" >/dev/null || {
    echo "Missing required tenant image release tool: $tool" >&2
    exit 127
  }
done
echo "Checking Docker Buildx"
docker buildx version >/dev/null
if [[ "$TENANT_IMAGE_SKIP_FINALIZE" != "1" ]]; then
  command -v cosign >/dev/null || {
    echo "Missing cosign for tenant image finalization" >&2
    exit 127
  }
  if ! docker sbom version >/dev/null 2>&1 && ! command -v syft >/dev/null; then
    echo "Missing docker sbom or syft" >&2
    exit 127
  fi
fi

# Callers pin their governed builder. The historical default remains for the
# workflows that have not yet migrated, with a fallback to the active default.
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
  echo "Tenant release tools and Buildx builder ${TENANT_IMAGE_BUILDER} are available"
  exit 0
fi

: "${TENANT_IMAGE_REPOSITORY:=registry.re8ch.com/image/re8ch-ai-operations}"
: "${TENANT_IMAGE_TAG:=${GITHUB_SHA:-$(git rev-parse HEAD)}}"
: "${TENANT_IMAGE_DOCKERFILE:=clusters/re8ch/components/ai-operations/Dockerfile}"
: "${TENANT_IMAGE_ARTIFACT_NAME:=tenant-infrastructure}"
: "${TENANT_IMAGE_PUSH_TIMEOUT_SECONDS:=300}"
if [[ ! "$TENANT_IMAGE_PUSH_TIMEOUT_SECONDS" =~ ^[1-9][0-9]{1,3}$ ]] ||
   (( TENANT_IMAGE_PUSH_TIMEOUT_SECONDS < 60 || TENANT_IMAGE_PUSH_TIMEOUT_SECONDS > 3600 )); then
  echo "TENANT_IMAGE_PUSH_TIMEOUT_SECONDS must be an integer from 60 through 3600" >&2
  exit 2
fi
dockerfile="$TENANT_IMAGE_DOCKERFILE"
image="${TENANT_IMAGE_REPOSITORY}:${TENANT_IMAGE_TAG}"
metadata=$(mktemp)
trap 'rm -f "$metadata"' EXIT

registry_username="${IMAGE_REGISTRY_USERNAME:-${HARBOR_USERNAME:-}}"
registry_password="${IMAGE_REGISTRY_PASSWORD:-${HARBOR_PASSWORD:-}}"
: "${registry_username:?registry username is required for image publication}"
: "${registry_password:?registry password is required for image publication}"
export IMAGE_REGISTRY_USERNAME="$registry_username" IMAGE_REGISTRY_PASSWORD="$registry_password"
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
credential = f"{os.environ['IMAGE_REGISTRY_USERNAME']}:{os.environ['IMAGE_REGISTRY_PASSWORD']}".encode()
config.setdefault("auths", {})[registry] = {
    "auth": base64.b64encode(credential).decode("ascii")
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(config, stream)
os.chmod(path, 0o600)
PY
else
  printf '%s' "$registry_password" | docker login "${TENANT_IMAGE_REPOSITORY%%/*}" --username "$registry_username" --password-stdin >/dev/null
fi

# The opt-in Engine path retains attestations only with the containerd store.
# Never silently downgrade this mode to the legacy classic-store publisher.
require_attestations="${TENANT_IMAGE_REQUIRE_ATTESTATIONS:-0}"
if [[ "$require_attestations" == "1" ]]; then
  [[ "${TENANT_IMAGE_DOCKER_PUSH:-0}" == "1" ]] || { echo "Attested Engine publication requires TENANT_IMAGE_DOCKER_PUSH=1" >&2; exit 2; }
  docker info --format '{{json .DriverStatus}}' | python3 -c '
import json, sys
if ["driver-type", "io.containerd.snapshotter.v1"] not in json.load(sys.stdin):
    sys.exit("Attested Engine publication requires the containerd image store")
'
fi

sbom_generator="${BUILDKIT_SBOM_GENERATOR:-docker.1ms.run/docker/buildkit-syft-scanner:stable-1}"
push_attempt=1
push_attempts="${TENANT_IMAGE_PUSH_ATTEMPTS:-3}"
while true; do
  if [[ "${TENANT_IMAGE_DOCKER_PUSH:-0}" == "1" ]]; then
    # Harbor's BuildKit registry exporter can stall indefinitely on layer
    # uploads. The Docker Engine uses a distinct, proven HTTP/1.1 upload path.
    # Classic callers retain their existing explicit provenance opt-out.
    # Opt-in containerd callers load and push the entire attested index.
    attestation_args=(--provenance=false)
    if [[ "$require_attestations" == "1" ]]; then
      attestation_args=(--provenance=mode=max --attest "type=sbom,generator=${sbom_generator}")
    fi
    if run_with_release_timeout "${TENANT_IMAGE_PUSH_TIMEOUT_SECONDS}s" \
         docker buildx build --builder "$TENANT_IMAGE_BUILDER" --platform linux/amd64 \
           "${attestation_args[@]}" --progress plain --load -f "$dockerfile" -t "$image" . \
       && run_with_release_timeout "${TENANT_IMAGE_PUSH_TIMEOUT_SECONDS}s" docker push "$image" \
       && run_with_release_timeout 60s docker buildx imagetools inspect "$image" \
            --format '{{json .Manifest}}' > "$metadata"; then
      break
    fi
  else
    if run_with_release_timeout "${TENANT_IMAGE_PUSH_TIMEOUT_SECONDS}s" \
         docker buildx build --builder "$TENANT_IMAGE_BUILDER" --platform linux/amd64 --provenance=mode=max \
           --attest "type=sbom,generator=${sbom_generator}" \
           --progress plain --push --metadata-file "$metadata" -f "$dockerfile" -t "$image" .; then
      break
    fi
  fi
  if (( push_attempt >= push_attempts )); then
    echo "Image build/push failed after ${push_attempt} attempts" >&2
    exit 1
  fi
  delay=$((push_attempt * 10))
  echo "Image build/push attempt ${push_attempt}/${push_attempts} failed; retrying with a fresh registry upload session in ${delay}s" >&2
  sleep "$delay"
  push_attempt=$((push_attempt + 1))
done
digest=$(python3 - "$metadata" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
print(value.get("containerimage.digest") or value["digest"])
PY
)
immutable="${TENANT_IMAGE_REPOSITORY}@${digest}"
if [[ "$require_attestations" == "1" ]]; then
  # Read back the published immutable index, not the mutable tag or local cache.
  run_with_release_timeout 120s docker buildx imagetools inspect "$immutable" --raw > "${TENANT_IMAGE_ARTIFACT_NAME}.index.json"
  run_with_release_timeout 120s docker buildx imagetools inspect "$immutable" --format '{{json .Provenance}}' > "${TENANT_IMAGE_ARTIFACT_NAME}.provenance.json"
  run_with_release_timeout 120s docker buildx imagetools inspect "$immutable" --format '{{json .SBOM}}' > "${TENANT_IMAGE_ARTIFACT_NAME}.sbom.json"
  python3 - "$TENANT_IMAGE_ARTIFACT_NAME" "$digest" "${GITHUB_SHA:-}" <<'PYATTEST'
import hashlib, json, re, sys
from pathlib import Path
name, digest, source = sys.argv[1:]
if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not re.fullmatch(r"[0-9a-f]{40}", source):
    sys.exit("Invalid immutable digest or source revision")
raw = Path(name + ".index.json").read_bytes()
if digest[7:] not in {hashlib.sha256(raw).hexdigest(), hashlib.sha256(raw.removesuffix(b"\n")).hexdigest()}:
    sys.exit("Published index digest does not match returned bytes")
index = json.loads(raw)
images = [m for m in index.get("manifests", []) if m.get("platform", {}).get("os") == "linux" and m.get("platform", {}).get("architecture") == "amd64"]
if len(images) != 1:
    sys.exit("Published index must contain exactly one linux/amd64 image")
subject = images[0]["digest"]
if not any(m.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest" and m.get("annotations", {}).get("vnd.docker.reference.digest") == subject for m in index["manifests"]):
    sys.exit("Published index lost its image-bound attestation manifest")
provenance = json.loads(Path(name + ".provenance.json").read_text()).get("linux/amd64", {}).get("SLSA", {})
sbom = json.loads(Path(name + ".sbom.json").read_text()).get("linux/amd64", {}).get("SPDX", {})
metadata = provenance.get("metadata", {}).get("https://mobyproject.org/buildkit@v1#metadata", {})
if provenance.get("buildType") != "https://mobyproject.org/buildkit@v1" or metadata.get("vcs", {}).get("revision") != source:
    sys.exit("Published provenance is absent or bound to a different source revision")
if not metadata.get("source", {}).get("infos"):
    sys.exit("Published provenance does not retain max-mode source metadata")
if not str(sbom.get("spdxVersion", "")).startswith("SPDX-") or not sbom.get("packages"):
    sys.exit("Published image lost its SPDX SBOM")
print("Published OCI index retained source-bound max provenance and SPDX SBOM")
PYATTEST
fi
printf '%s\n' "$immutable" | tee "${TENANT_IMAGE_ARTIFACT_NAME}.image"
if [[ "${TENANT_IMAGE_SKIP_FINALIZE:-}" == "1" ]]; then
  echo "Image published; SBOM and signing delegated to the hosted finalize job"
  exit 0
fi
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
