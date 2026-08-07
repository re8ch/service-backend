#!/usr/bin/env bash
set -euo pipefail

allowed_tenant="__AKASH_TEST_TENANT__"
[[ "$allowed_tenant" == akash1* ]] || { printf 'test tenant is not configured\n' >&2; exit 1; }
[[ "${AKASH_OWNER:-}" == "$allowed_tenant" ]] || { printf 'tenant is not allowlisted\n' >&2; exit 1; }

request="$(jq -c 'if type == "object" and has("resources") then .resources else . end' < /dev/stdin)"
jq -e '
  (map(.cpu * .count) | add) <= 8000 and
  (map(.memory * .count) | add) <= (16 * 1024 * 1024 * 1024) and
  ([.[] | (.storage[]? | select(.class == "ephemeral").size // 0) * .count] | add // 0) <= (100 * 1024 * 1024 * 1024) and
  (map((.gpu.units // 0) * .count) | add // 0) == 0 and
  (map((.ip_lease_quantity // 0) * .count) | add // 0) == 0 and
  ([.[] | .storage[]? | select(.class != "ephemeral" and .class != "ram")] | length) == 0
' <<<"$request" >/dev/null || { printf 'order exceeds sandbox policy\n' >&2; exit 1; }

# One uACT/block makes the operator-owned Sandbox smoke bid deterministic.
printf '1\n'
