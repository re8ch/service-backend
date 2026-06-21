#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HARBOR_SRC="${HARBOR_SRC:-https://github.com/goharbor/harbor.git}"
HARBOR_TAG="${HARBOR_TAG:-v2.15.1}"
DISTRIBUTION_SRC="${DISTRIBUTION_SRC:-https://github.com/goharbor/distribution.git}"
REGISTRY_SRC_TAG="${REGISTRY_SRC_TAG:-v2.8.3-harbor.1-rc.1}"
GOBUILDIMAGE="${GOBUILDIMAGE:-golang:1.25.9}"
BASE_NAMESPACE="${BASE_NAMESPACE:-goharbor}"
BASE_IMAGE_VERSION="${BASE_IMAGE_VERSION:-v2.15.1}"
IMAGE="${IMAGE:-registry.re8ch.com/functions-shared/harbor-registry-photon:v2.15.1-oss-esa}"
PLATFORM="${PLATFORM:-linux/amd64}"
PUSH="${PUSH:-false}"
RUN_TESTS="${RUN_TESTS:-true}"

PATCH_FILE="${PATCH_FILE:-$ROOT_DIR/patches/harbor-distribution/oss-redirectendpoint-preserve-signed-query.patch}"
WORKDIR="${WORKDIR:-$(mktemp -d "${TMPDIR:-/tmp}/harbor-registry-oss-esa.XXXXXX")}"

cleanup() {
  if [[ "${KEEP_WORKDIR:-false}" != "true" ]]; then
    rm -rf "$WORKDIR"
  else
    echo "Keeping workdir: $WORKDIR" >&2
  fi
}
trap cleanup EXIT

docker_build() {
  if [[ -n "${DOCKER_NETWORK:-}" ]]; then
    docker build --platform="$PLATFORM" --network="$DOCKER_NETWORK" "$@"
  else
    docker build --platform="$PLATFORM" "$@"
  fi
}

echo "Workdir: $WORKDIR"
echo "Image: $IMAGE"
echo "Platform: $PLATFORM"

git clone --depth 1 --branch "$HARBOR_TAG" "$HARBOR_SRC" "$WORKDIR/harbor"
git clone --depth 1 --branch "$REGISTRY_SRC_TAG" "$DISTRIBUTION_SRC" "$WORKDIR/distribution"

git -C "$WORKDIR/distribution" apply "$PATCH_FILE"

if [[ "$RUN_TESTS" == "true" ]]; then
  test_gopath="$WORKDIR/gopath"
  mkdir -p "$test_gopath/src/github.com/docker"
  ln -s "$WORKDIR/distribution" "$test_gopath/src/github.com/docker/distribution"
  GO111MODULE=off GOPATH="$test_gopath" go test -tags include_oss \
    github.com/docker/distribution/registry/storage/driver/oss \
    -run 'TestRewriteSignedURLHost' -count=1
fi

cp "$WORKDIR/harbor/make/photon/registry/Dockerfile.binary" "$WORKDIR/distribution/Dockerfile.binary"

docker_build \
  --build-arg golang_image="$GOBUILDIMAGE" \
  -f "$WORKDIR/distribution/Dockerfile.binary" \
  -t harbor-registry-golang-oss-esa:build \
  "$WORKDIR/distribution"

mkdir -p "$WORKDIR/harbor/make/photon/registry/binary"
container_id="$(docker create --platform "$PLATFORM" harbor-registry-golang-oss-esa:build)"
trap 'docker rm -f "$container_id" >/dev/null 2>&1 || true; cleanup' EXIT
docker cp "$container_id:/go/src/github.com/docker/distribution/bin/registry" \
  "$WORKDIR/harbor/make/photon/registry/binary/registry"
docker rm -f "$container_id" >/dev/null
container_id=""

docker_build \
  --build-arg harbor_base_image_version="$BASE_IMAGE_VERSION" \
  --build-arg harbor_base_namespace="$BASE_NAMESPACE" \
  -f "$WORKDIR/harbor/make/photon/registry/Dockerfile" \
  -t "$IMAGE" \
  "$WORKDIR/harbor"

if [[ "$PUSH" == "true" ]]; then
  docker push "$IMAGE"
fi

echo "Built $IMAGE"
