#!/usr/bin/env bash
set -euo pipefail
source_repo="$OWNER/$REPO"
title="Inspection $source_repo · $GITHUB_RUN_ID"
audit="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
dedupe="$source_repo:$GITHUB_RUN_ID"
if [[ -n "${DELIVERY:-}" ]]; then
  title="Release receipt $source_repo · $DELIVERY"
  dedupe="$source_repo:$DELIVERY"
fi
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
if [[ -n "${DELIVERY:-}" && -z "${RECEIPT_EXECUTOR_ID:-}" ]]; then
  signal="Receipt observed; no correlated executor run or managed-object readback"
  dedupe="$dedupe:receipt-only"
elif run=$(gh api "$endpoint" --jq "$selector"); then
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
action="Observe Only"
readback=""
if [[ -n "${RECEIPT_EXECUTOR_ID:-}" && "$source_repo" != strata-silica/storage-gitops ]]; then
  directory=$(mktemp -d "$RUNNER_TEMP/readback.XXXXXX")
  if gh run download "$RECEIPT_EXECUTOR_ID" -R "$source_repo" --pattern '*-reconcile-*' --dir "$directory"; then
    mapfile -t reports < <(find "$directory" -type f -name result.json | sort)
    if (( ${#reports[@]} )); then
      readback=$(jq -sr --arg delivery "$DELIVERY" '
        map(select(.delivery==$delivery and (.digest|type)=="string")) |
        map("- " + .release + " " + .digest + " " + .outcome + " (" + ((.objects|length)|tostring) + " objects)" + (if .checking then " at " + .checking else "" end) + (if .reason then " " + .reason else "" end)) | join("\n")
      ' "${reports[@]}")
      if jq -se --arg d "$DELIVERY" 'any(.[]; .delivery==$d and (.applied|length)>0)' "${reports[@]}" >/dev/null; then action=Reconcile; fi
    fi
  fi
  if [[ -z "$readback" ]]; then
    state=Unknown
    signal="Executor readback unavailable"
  fi
fi
body="$signal. Evidence: $logs. Inspection: $audit."
body+=$'\n'"$readback"
project=$(gh project view "$BOARD" --owner "$OWNER" --format json --jq .id)
items=$(gh project item-list "$BOARD" --owner "$OWNER" --limit 1000 --format json)
if [[ -n "${DELIVERY:-}" ]]; then
  body+=" Release $RELEASE_ID. Kubernetes receipt: gitops-events/release-$DELIVERY."
  if jq -e --arg key "$dedupe" 'any(.items[]; ."dedupe Key"==$key)' <<<"$items" >/dev/null; then exit 0; fi
fi
prior=$(jq -c --arg prefix "Inspection $source_repo · " --arg title "$title" '[.items[]|select((.title|startswith($prefix)) and .title!=$title)]|max_by(.title|split(" · ")[-1]|tonumber)' <<<"$items")
previous=$(jq -r '."workflow Run ID" // ""' <<<"$prior")
prior_state=$(jq -r '."observation State" // "Unknown"' <<<"$prior")
attempt=$(jq -r '.run_attempt // 1' <<<"${run:-null}")
fresh="new source run"
if [[ "$source_id" == "$previous" && -n "$source_id" ]]; then
  fresh="previously observed source run"
  if [[ $(jq -r '.content.body' <<<"$prior") != *"attempt $attempt."* && "$attempt" != 1 ]]; then fresh="new source attempt"; fi
fi
transition=""
if [[ "$state" == Failed ]]; then
  transition=新增
  [[ "$prior_state" != Failed ]] || transition=持续
elif [[ "$state" == Healthy && "$prior_state" == Failed ]]; then
  transition=已恢复
fi
body="$body Source: $fresh, attempt $attempt."
if [[ "$state" == Failed ]]; then
  jobs=$(gh api --paginate --slurp "repos/$source_repo/actions/runs/$source_id/attempts/$attempt/jobs?per_page=100")
  while IFS=$'\t' read -r job_id job step; do
    [[ -n "$job_id" ]] || continue
    reason="Source job reported failure; no classified diagnostic"
    if gh api "repos/$source_repo/actions/jobs/$job_id/logs" > "$RUNNER_TEMP/job.log" 2>/dev/null; then
      for cause in 'apiserver not ready' 'accepted bundle is outside the main-domain repository' 'restricted age identity was not provisioned' 'Apply failed with 1 conflict' 'context deadline exceeded'; do
        if grep -F "$cause" "$RUNNER_TEMP/job.log" | grep -vE '##\[group\]|\[36;1m' >/dev/null; then reason="$cause"; break; fi
      done
    fi
    rm -f "$RUNNER_TEMP/job.log"
    body+=$'\n'"- $job | $step | $reason | $logs/job/$job_id"
  done < <(jq -r '.[]|.jobs[]|select(.conclusion=="failure" or .conclusion=="timed_out")|[.id,.name,([.steps[]?|select(.conclusion=="failure" or .conclusion=="timed_out")|.name]|join(", ")|if .=="" then "job termination" else . end)]|@tsv' <<<"$jobs")
fi
item=$(jq -r --arg title "$title" '.items[]|select(.title==$title)|.id' <<<"$items")
if [[ -z "$item" ]]; then
  item=$(gh project item-create "$BOARD" --owner "$OWNER" --title "$title" --body "$body" --format json --jq .id)
else
  draft=$(jq -r --arg i "$item" '.items[]|select(.id==$i)|.content.id' <<<"$items")
  gh project item-edit --id "$draft" --title "$title" --body "$body"
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

if [[ -n "$revision" ]]; then
  edit 'Revision' --text "$revision"
elif [[ -n "${DELIVERY:-}" ]]; then
  revision_field=$(jq -er '.fields[]|select(.name=="Revision")|.id' <<<"$fields")
  gh project item-edit --project-id "$project" --id "$item" --field-id "$revision_field" --clear
fi
select_value 'Observation State' "$state"
select_value 'Reconcile Action' "$action"
edit 'Dedupe Key' --text "$dedupe"
echo "$body" >> "$GITHUB_STEP_SUMMARY"
number=$(gh api graphql -f query='query($id:ID!){node(id:$id){... on ProjectV2Item{fullDatabaseId}}}' -f id="$item" --jq .data.node.fullDatabaseId)
: > "$RUNNER_TEMP/observation-summary.txt"
if [[ -n "$transition" ]]; then
  printf '%s | %s | %s | [明细](https://github.com/orgs/%s/projects/%s?pane=issue&itemId=%s)\n' "$transition" "$source_repo" "$fresh" "$OWNER" "$BOARD" "$number" > "$RUNNER_TEMP/observation-summary.txt"
fi
