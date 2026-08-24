#!/bin/sh
set -eu

PLANET_FILE=${1:-/Volumes/A1/planet}
IMAGE_TAG=${IMAGE_TAG:-re8ch/zerotier-one-custom-planet:1.10.3-armv7}

if [ ! -f "${PLANET_FILE}" ]; then
  echo "missing planet file: ${PLANET_FILE}" >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)
PLANET_CONTEXT=$(mktemp -d "${TMPDIR:-/tmp}/zt-planet.XXXXXX")

cleanup() {
  rm -rf "${PLANET_CONTEXT}"
}
trap cleanup EXIT INT TERM

cp "${PLANET_FILE}" "${PLANET_CONTEXT}/planet"

docker buildx build \
  --platform linux/arm/v7 \
  -f "${SCRIPT_DIR}/Dockerfile.armv7" \
  --build-context "planet=${PLANET_CONTEXT}" \
  -t "${IMAGE_TAG}" \
  --load \
  "${SCRIPT_DIR}"

echo "built ${IMAGE_TAG}"
