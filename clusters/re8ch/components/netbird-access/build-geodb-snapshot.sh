#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_REVISION:?Pin a full meta-rules-dat commit SHA}"
: "${OUTPUT:=../egress-fabric/geodb/geodb.json.gz}"

build_dir="$(mktemp -d)"
trap 'rm -rf "${build_dir}"' EXIT
git clone --filter=blob:none --no-checkout https://github.com/MetaCubeX/meta-rules-dat.git "${build_dir}/meta"
git -C "${build_dir}/meta" sparse-checkout init --cone
git -C "${build_dir}/meta" sparse-checkout set geo/geosite geo/geoip
git -C "${build_dir}/meta" checkout --detach "${SOURCE_REVISION}"
python3 "$(dirname "$0")/build-geodb-snapshot.py" \
  --source-dir "${build_dir}/meta" \
  --source-revision "${SOURCE_REVISION}" \
  --output "${OUTPUT}"
