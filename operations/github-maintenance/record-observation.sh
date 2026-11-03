#!/usr/bin/env bash
set -euo pipefail
source_repo="$OWNER/$REPO"
title="Inspection $source_repo · $GITHUB_RUN_ID"
audit="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
state=Unknown
signal="No completed reconcile run available"
logs="$audit"
revision=""
source_id=""
if run=$(gh api "repos/$source_repo/actions/workflows/$WORKFLOW/runs?branch=main&status=completed&per_page=1" --jq '.workflow_runs[0] // empty'); then
  if [[ -n "$run" ]]; then
    source_id=$(jq -r .id <<<"$run")
    logs=$(jq -r .html_url <<<"$run")
    revision=$(jq -r .head_sha <<<"$run")
    signal="Latest completed Action: $(jq -r .conclusion <<<"$run") at $(jq -r .updated_at <<<"$run")"
    if [[ $(jq -r .conclusion <<<"$run") == failure ]]; then state=Failed; fi
  fi
else
  signal="Actions evidence read failed"
fi
body="$signal. Evidence: $logs. Inspection: $audit."
project=$(gh project view "$BOARD" --owner "$OWNER" --format json --jq .id)
items=$(gh project item-list "$BOARD" --owner "$OWNER" --limit 1000 --format json)
item=$(jq -r --arg title "$title" '.items[]|select(.title==$title)|.id' <<<"$items")
if [[ -z "$item" ]]; then
  item=$(gh project item-create "$BOARD" --owner "$OWNER" --title "$title" --body "$body" --format json --jq .id)
fi
fields=$(gh project field-list "$BOARD" --owner "$OWNER" --format json)
edit() {
  local id
  id=$(jq -er --arg f "$1" '.fields[]|select(.name==$f)|.id' <<<"$fields")
  gh project item-edit --project-id "$project" --id "$item" --field-id "$id" "$2" "$3"
}
select_value() {
  local option
  option=$(jq -er --arg f "$1" --arg v "$2" '.fields[]|select(.name==$f)|.options[]|select(.name==$v)|.id' <<<"$fields")
  edit "$1" --single-select-option-id "$option"
}
edit 'Observed At' --date "$(date -u +%F)"
edit 'Workflow Run ID' --text "${source_id:-$GITHUB_RUN_ID}"
edit 'Related Logs' --text "$logs"
edit 'Signal' --text "$signal"
edit 'Dedupe Key' --text "$source_repo:$GITHUB_RUN_ID"
if [[ -n "$revision" ]]; then edit 'Revision' --text "$revision"; fi
select_value 'Observation State' "$state"
select_value 'Reconcile Action' 'Observe Only'
echo "$body" >> "$GITHUB_STEP_SUMMARY"
printf '%s | %s | https://github.com/orgs/%s/projects/%s\n' "$source_repo" "$signal" "$OWNER" "$BOARD" > "$RUNNER_TEMP/observation-summary.txt"
