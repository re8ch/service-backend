#!/usr/bin/env bash
# One-off pre-publication audit. No raw logs, artifacts or credentials are retained.
set -euo pipefail
umask 077
export AUDIT_ROOT="$RUNNER_TEMP/publication-audit"
mkdir -p "$AUDIT_ROOT/reports" "$AUDIT_ROOT/raw" "$AUDIT_ROOT/bin"
trap 'rm -rf "$AUDIT_ROOT/raw" "$AUDIT_ROOT/bin"' EXIT
image=zricethezav/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
docker run --rm --entrypoint sh "$image" -c 'cat "$(command -v gitleaks)"' > "$AUDIT_ROOT/bin/gitleaks"
chmod 700 "$AUDIT_ROOT/bin/gitleaks"
"$AUDIT_ROOT/bin/gitleaks" version
# GitHub caps filtered workflow-run searches at 1,000; filter only after pagination.
gh api --paginate 'repos/re8ch/service-backend/actions/runs?per_page=100' \
  --jq '.workflow_runs[] | select(.status == "completed") | "run \(.id)"' > "$AUDIT_ROOT/targets"
gh api --paginate 'repos/re8ch/service-backend/actions/artifacts?per_page=100' \
  --jq '.artifacts[] | select(.expired == false) | "artifact \(.id)"' >> "$AUDIT_ROOT/targets"
sort -u "$AUDIT_ROOT/targets" -o "$AUDIT_ROOT/targets"
awk '{n[$1]++} END {for (kind in n) print kind, n[kind]}' "$AUDIT_ROOT/targets" > "$AUDIT_ROOT/reports/inventory.txt"
# Keep the one-off audit bounded, and fail closed on unexpectedly large inventories.
test "$(wc -l < "$AUDIT_ROOT/targets")" -le 15000
scan_record() {
  local kind="$1" id="$2" path output result
  [[ "$id" =~ ^[0-9]+$ ]] || return 1
  path="actions/runs/$id/logs"
  [ "$kind" != artifact ] || path="actions/artifacts/$id/zip"
  output="$AUDIT_ROOT/raw/$kind-$id"
  mkdir -p "$output"
  if ! gh api "repos/re8ch/service-backend/$path" > "$output/archive.zip" 2>"$output/error"; then
    # Expired or removed logs cannot be exposed through the download endpoint.
    if grep -Eq 'HTTP (404|410)' "$output/error"; then
      printf '%s %s unavailable\n' "$kind" "$id" > "$AUDIT_ROOT/reports/$kind-$id.result"
    else
      printf '%s %s download-error\n' "$kind" "$id" > "$AUDIT_ROOT/reports/$kind-$id.result"
    fi
  else
    result=0
    "$AUDIT_ROOT/bin/gitleaks" dir "$output" --redact --no-banner --max-archive-depth=4 --max-decode-depth=5 \
      --report-format json --report-path "$AUDIT_ROOT/reports/$kind-$id.json" > "$output/scan.log" 2>&1 || result=$?
    case "$result" in
      0) printf '%s %s clean\n' "$kind" "$id" ;;
      1) printf '%s %s findings\n' "$kind" "$id" ;;
      *) printf '%s %s scan-error\n' "$kind" "$id" ;;
    esac > "$AUDIT_ROOT/reports/$kind-$id.result"
  fi
  rm -rf "$output"
}
export -f scan_record
xargs -P 8 -n 2 bash -c 'scan_record "$1" "$2"' _ < "$AUDIT_ROOT/targets"
cat "$AUDIT_ROOT"/reports/*.result > "$AUDIT_ROOT/reports/summary.txt"
awk '{n[$3]++} END {for (s in n) print s, n[s]}' "$AUDIT_ROOT/reports/summary.txt"
! grep -Eq ' (findings|download-error|scan-error)$' "$AUDIT_ROOT/reports/summary.txt"
