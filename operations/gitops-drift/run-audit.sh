#!/bin/sh
set -eu

root=${RE8CH_SERVICE_BACKEND_ANCHOR:-/Volumes/A1/service-backend}
state_dir=${RE8CH_GITOPS_DRIFT_STATE_DIR:-"$HOME/Library/Application Support/RE8CH/gitops-drift"}
mkdir -p "$state_dir"
tmp=$(mktemp "$state_dir/report.XXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM
audit=${RE8CH_GITOPS_DRIFT_AUDIT:-"$root/operations/gitops-drift/audit.py"}
python3 "$audit" --output json >"$tmp" || status=$?
mv "$tmp" "$state_dir/latest.json"
trap - EXIT HUP INT TERM
exit "${status:-0}"
