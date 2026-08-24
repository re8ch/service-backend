#!/usr/bin/env bash
set -euo pipefail

base_dir="$(cd "$(dirname "$0")" && pwd)"
: "${KUBECTL:=sudo k3s kubectl}"
: "${MIN_READY_RELAYS:=1}"

${KUBECTL} get --raw=/readyz | grep -qx ok
for node in r640 rancher-panel overseas-edge-50; do
  [[ "$(${KUBECTL} get node "$node" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" == True ]]
done

# Regional CN relay nodes are an N-of-M pool. Low-bandwidth or intermittently
# connected candidates may remain advertised because NetBird clients probe and
# skip unavailable relays; they are not individual hard dependencies.
relay_json="$(${KUBECTL} -n netbird-access get deploy \
  netbird-relay-gz-1 netbird-relay-hz-1 -o json)"
ready_relays="$(jq '[.items[] | select((.status.availableReplicas // 0) > 0)] | length' <<<"$relay_json")"
if (( ready_relays < MIN_READY_RELAYS )); then
  echo "Relay quorum failed: ${ready_relays}/2 available, need ${MIN_READY_RELAYS}" >&2
  exit 1
fi
jq -r '.items[] | "relay_candidate=" + .metadata.name + " available=" + ((.status.availableReplicas // 0)|tostring)' <<<"$relay_json"

# Keep the first exact rollback copy. Re-running this script must not replace it
# with an already migrated configuration.
if ! ${KUBECTL} -n netbird-access get secret \
  netbird-server-config-pre-external-relay-20260809 >/dev/null 2>&1; then
  ${KUBECTL} -n netbird-access get secret netbird-server-config -o json \
    | jq '.metadata={name:"netbird-server-config-pre-external-relay-20260809",namespace:"netbird-access"} | del(.status)' \
    | ${KUBECTL} apply -f - >/dev/null
fi

${KUBECTL} -n netbird-access get secret netbird-server-config -o json \
  | python3 "$base_dir/configure-external-relays.py" \
  | ${KUBECTL} apply -f - >/dev/null

${KUBECTL} -n netbird-access rollout restart deploy/netbird-server
${KUBECTL} -n netbird-access rollout status deploy/netbird-server --timeout=5m
