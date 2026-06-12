#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREPULL_MANIFEST="${PREPULL_MANIFEST:-${ROOT_DIR}/manifests/k3s/qwen/qwen-image-prepull.yaml}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d%H%M%S)}"
TAG_PREFIX="${TAG_PREFIX:-security-go1264}"
LOG_DIR="${LOG_DIR:-${ROOT_DIR}/.build/security-rollout/logs/${RUN_ID}}"
SUMMARY="${SUMMARY:-${ROOT_DIR}/.build/security-rollout/qwen-${RUN_ID}.tsv}"
LIMIT="${LIMIT:-}"

mkdir -p "${LOG_DIR}" "$(dirname "${SUMMARY}")"
printf 'function_dir\tfunction_name\timage\tdigest\tharbor_status\tharbor_critical\tharbor_high\tharbor_medium\tharbor_fixable\tsbom\taccessories\ttrivy_critical\ttrivy_high\ttrivy_medium\ttrivy_low\ttrivy_fixable\n' >"${SUMMARY}"

targets_file="${LOG_DIR}/targets.tsv"
python3 - "${ROOT_DIR}" "${PREPULL_MANIFEST}" >"${targets_file}" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = Path(sys.argv[2])
func_dirs = {p.parent.name for p in (root / "functions").glob("*/main.go")}
override = {
    "qianwen-prod-api-gateway": "edge-router",
    "qianwen-api-gateway": "edge-router",
}

for raw in manifest.read_text().splitlines():
    line = raw.strip()
    if not line.startswith("registry.re8ch.com/qianwen/"):
        continue
    repo_tag = line.split("/qianwen/", 1)[1]
    repo = re.split(r"[:@]", repo_tag, 1)[0]
    function_name = repo.removeprefix("processor-")
    function_dir = function_name
    for prefix in ("qianwen-prod-", "qianwen-"):
        if function_dir.startswith(prefix):
            function_dir = function_dir[len(prefix):]
            break
    function_dir = override.get(function_name, function_dir)
    if function_dir not in func_dirs:
        print(f"MISSING\t{function_name}\t{function_dir}", file=sys.stderr)
        continue
    print(f"{function_dir}\t{function_name}")
PY
total="$(wc -l <"${targets_file}" | tr -d ' ')"

count=0
while IFS=$'\t' read -r function_dir function_name; do
  [[ -z "${function_dir}" ]] && continue
  if [[ -n "${LIMIT}" && "${count}" -ge "${LIMIT}" ]]; then
    break
  fi
  count=$((count + 1))
  tag="${TAG_PREFIX}-${RUN_ID}"
  image="registry.re8ch.com/qianwen/processor-${function_name}:${tag}"
  log_file="${LOG_DIR}/${function_name}.log"

  printf '[%02d/%02d] build/sign/scan %s -> %s\n' "${count}" "${total}" "${function_name}" "${tag}"
  if ! (
    cd "${ROOT_DIR}"
    PATH="${PATH}:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin" \
      TENANT=qianwen \
      "${ROOT_DIR}/scripts/build-sign-scan-function.sh" "${function_dir}" "${function_name}" "${tag}" </dev/null
  ) >"${log_file}" 2>&1; then
    echo "Failed: ${function_name}. Log: ${log_file}" >&2
    tail -80 "${log_file}" >&2
    exit 1
  fi

  digest="$(grep '^digest=' "${log_file}" | tail -1 | cut -d= -f2-)"
  harbor="$(grep '^harbor_scan' "${log_file}" | tail -1 | cut -f2-)"
  trivy="$(grep '^trivy_direct' "${log_file}" | tail -1 | cut -f2-)"
  IFS=$'\t' read -r harbor_status harbor_critical harbor_high harbor_medium harbor_fixable sbom accessories <<<"${harbor}"
  IFS=$'\t' read -r trivy_critical trivy_high trivy_medium trivy_low trivy_fixable <<<"${trivy}"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${function_dir}" "${function_name}" "${image}" "${digest}" \
    "${harbor_status}" "${harbor_critical}" "${harbor_high}" "${harbor_medium}" "${harbor_fixable}" "${sbom}" "${accessories}" \
    "${trivy_critical}" "${trivy_high}" "${trivy_medium}" "${trivy_low}" "${trivy_fixable}" \
    >>"${SUMMARY}"
  printf '  digest=%s harbor=%s/%s/%s/%s trivy=%s/%s/%s/%s fixable=%s\n' \
    "${digest}" "${harbor_critical}" "${harbor_high}" "${harbor_medium}" "${harbor_fixable}" \
    "${trivy_critical}" "${trivy_high}" "${trivy_medium}" "${trivy_low}" "${trivy_fixable}"
done <"${targets_file}"

printf 'summary=%s\n' "${SUMMARY}"
