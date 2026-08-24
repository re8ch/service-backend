#!/usr/bin/env bash
set -euo pipefail

: "${CASE_ID:?A, B, C, or D}"
: "${DESTINATION:?hostname or IP}"
: "${TRACE_ID:?unique trace id}"
: "${OUTPUT_DIR:?existing private output directory}"

case "$(uname -s)" in
  Linux) ip route get "${DESTINATION}" >"${OUTPUT_DIR}/${CASE_ID}-os-route.txt" ;;
  Darwin) route -n get "${DESTINATION}" >"${OUTPUT_DIR}/${CASE_ID}-os-route.txt" ;;
  *) echo "Use Get-NetRoute and Test-NetConnection on Windows" >&2; exit 2 ;;
esac

netbird status --detail >"${OUTPUT_DIR}/${CASE_ID}-netbird-status.txt"
netbird routes list >"${OUTPUT_DIR}/${CASE_ID}-netbird-routes.txt"
curl --fail --silent --show-error \
  -H "X-RE8CH-Trace-ID: ${TRACE_ID}" \
  "https://${DESTINATION}/whoami?trace_id=${TRACE_ID}" \
  >"${OUTPUT_DIR}/${CASE_ID}-whoami.json"

printf '%s\n' "Evidence collected locally. Add Geo, Hubble, BGP and upstream next-hop evidence from the operator runbook."
