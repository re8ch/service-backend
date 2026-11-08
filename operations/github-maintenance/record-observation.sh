#!/usr/bin/env bash
set -euo pipefail

source_repo="$OWNER/$REPO"
audit="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
dedupe="$source_repo:${DELIVERY:-$GITHUB_RUN_ID}"
state=Unknown
signal="No completed reconcile run available"
logs="$audit"
revision=""
source_id=""
endpoint="repos/$source_repo/actions/workflows/$WORKFLOW/runs?branch=main&status=completed&per_page=1"
selector='.workflow_runs[0] // empty'
if [[ -n "${RECEIPT_EXECUTOR_ID:-}" ]]; then
  endpoint="repos/$source_repo/actions/runs/$RECEIPT_EXECUTOR_ID"
  selector='select(.status=="completed")'
  dedupe="$dedupe:$RECEIPT_EXECUTOR_ID"
fi

source_gh() { GH_TOKEN="$SOURCE_GH_TOKEN" gh "$@"; }
if [[ -n "${DELIVERY:-}" && -z "${RECEIPT_EXECUTOR_ID:-}" ]]; then
  signal="Receipt observed; no correlated executor run or managed-object readback"
  dedupe="$dedupe:receipt-only"
elif run=$(source_gh api "$endpoint" --jq "$selector"); then
  if [[ -n "$run" ]]; then
    source_id=$(jq -r .id <<<"$run")
    logs=$(jq -r .html_url <<<"$run")
    revision=$(jq -r .head_sha <<<"$run")
    signal="Latest completed Action: $(jq -r .conclusion <<<"$run") at $(jq -r .updated_at <<<"$run")"
    case $(jq -r .conclusion <<<"$run") in
      success) state=Healthy ;;
      failure|timed_out|action_required|startup_failure) state=Failed ;;
    esac
  fi
else
  signal="Actions evidence read failed"
fi

if [[ -z "${DELIVERY:-}" && -n "$source_id" ]]; then
  dedupe="$source_repo:$source_id:$(jq -r '.run_attempt // 1' <<<"$run")"
fi

action="Observe Only"
readback=""
if [[ -n "$source_id" ]]; then
  directory=$(mktemp -d "$RUNNER_TEMP/readback.XXXXXX")
  if GH_TOKEN="$SOURCE_GH_TOKEN" python operations/github-maintenance/download_reconcile_evidence.py "$source_repo" "$source_id" "$directory"; then
    evidence=$(python operations/github-maintenance/reconcile_evidence.py "$directory" "$(jq -r .conclusion <<<"$run")" --delivery "${DELIVERY:-}")
    state=$(jq -r .state <<<"$evidence")
    action=$(jq -r .action <<<"$evidence")
    readback=$(jq -r '.reports[] | "- " + .release + " " + (.digest // "digest unavailable") + " " + .outcome + " health=" + (.health // "receipt readback") + (if .warning != "" then " " + .warning else "" end)' <<<"$evidence")
    signal+="; release evidence: $state"
  else
    [[ "$state" == Failed ]] || state=Unknown
    signal+="; release readback unavailable"
  fi
fi

attempt=$(jq -r '.run_attempt // 1' <<<"${run:-null}")
body_file=$(mktemp "$RUNNER_TEMP/reconcile-record.XXXXXX")
{
  printf '%s. Evidence: %s. Inspection: %s.\n' "$signal" "$logs" "$audit"
  [[ -z "$readback" ]] || printf '%s\n' "$readback"
  printf '\n- Source: `%s`\n- State: `%s`\n- Action: `%s`\n- Revision: `%s`\n- Source run: `%s` (attempt %s)\n- Dedupe key: `%s`\n' \
    "$source_repo" "$state" "$action" "${revision:-unavailable}" "${source_id:-unavailable}" "$attempt" "$dedupe"
  if [[ -n "${DELIVERY:-}" ]]; then
    printf -- '- Release: `%s`\n- Kubernetes receipt: `gitops-events/release-%s`\n' "$RELEASE_ID" "$DELIVERY"
  fi
} > "$body_file"

python operations/github-maintenance/record_reconcile.py \
  --state "$state" --source-repository "$source_repo" --dedupe-key "$dedupe" --body-file "$body_file" \
  --wiki-repository "${JOURNAL_REPOSITORY:-$source_repo}" \
  --incident-repository "${INCIDENT_REPOSITORY:-re8ch/service-backend}" \
  --project-owner "${MAINTENANCE_PROJECT_OWNER:-re8ch}" \
  --project-number "${MAINTENANCE_PROJECT_NUMBER:-1}"

cat "$body_file" >> "$GITHUB_STEP_SUMMARY"
printf '%s | %s | %s\n' "$state" "$source_repo" "$logs" > "$RUNNER_TEMP/observation-summary.txt"
