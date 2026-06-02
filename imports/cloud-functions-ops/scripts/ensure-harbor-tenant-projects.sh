#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Ensure Harbor tenant projects exist.

Usage:
  HARBOR_USERNAME=admin HARBOR_PASSWORD=... ./scripts/ensure-harbor-tenant-projects.sh qianwen ledger anycam

Environment:
  HARBOR_URL       default: https://registry.re8ch.com
  HARBOR_USERNAME required
  HARBOR_PASSWORD required
  PROJECT_PUBLIC  default: false

The script only creates projects. Create per-project robot accounts in Harbor
or automation after confirming the Harbor version's robot-account API shape.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

HARBOR_URL="${HARBOR_URL:-https://registry.re8ch.com}"
PROJECT_PUBLIC="${PROJECT_PUBLIC:-false}"
HARBOR_USERNAME="${HARBOR_USERNAME:-}"
HARBOR_PASSWORD="${HARBOR_PASSWORD:-}"

if [[ -z "${HARBOR_USERNAME}" || -z "${HARBOR_PASSWORD}" ]]; then
  usage
  exit 2
fi

if [[ "$#" -eq 0 ]]; then
  set -- qianwen ledger anycam bench functions-shared
fi

api() {
  curl -fsS -u "${HARBOR_USERNAME}:${HARBOR_PASSWORD}" "$@"
}

for project in "$@"; do
  if [[ ! "${project}" =~ ^[a-z0-9][a-z0-9._-]*$ ]]; then
    echo "Invalid Harbor project name: ${project}" >&2
    exit 2
  fi

  encoded="${project// /%20}"
  if api "${HARBOR_URL}/api/v2.0/projects?name=${encoded}" | jq -e 'length > 0' >/dev/null; then
    printf 'exists: %s\n' "${project}"
    continue
  fi

  payload="$(jq -cn --arg name "${project}" --argjson public "${PROJECT_PUBLIC}" '{
    project_name: $name,
    public: $public,
    metadata: {
      public: ($public | tostring),
      enable_content_trust: "false",
      prevent_vul: "false",
      severity: "none",
      auto_scan: "false"
    }
  }')"
  api -X POST "${HARBOR_URL}/api/v2.0/projects" \
    -H 'Content-Type: application/json' \
    -d "${payload}" >/dev/null
  printf 'created: %s\n' "${project}"
done

