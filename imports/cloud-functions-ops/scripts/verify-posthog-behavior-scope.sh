#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
semantic_sql="$ROOT/sql/004_qwen_posthog_semantic_export.sql"
analytics_go="$ROOT/functions/analytics/main.go"
forbidden='(row_to_json|to_json[[:space:]]*\(|select[[:space:]]+\*|\bt\.\*|jsonb_agg[[:space:]]*\([[:space:]]*t|json_agg[[:space:]]*\([[:space:]]*t|prompt|content|asset_content|oss_path|file_path|result_url|raw_url|error_stack|stack_trace|request_body|response_body|payload)'

matches="$(rg -n -i "$forbidden" "$semantic_sql" || true)"
if [[ -n "$matches" ]]; then
  echo "PostHog semantic export touches content/replay fields:" >&2
  echo "$matches" >&2
  exit 1
fi

for required in ANALYTICS_SEMANTIC_EXPORT_MAX_ROWS ANALYTICS_SEMANTIC_EXPORT_MAX_BYTES total_bytes max_event_bytes sample_events; do
  if ! rg -q "$required" "$analytics_go"; then
    echo "Analytics semantic export is missing bandwidth guard/reporting token: $required" >&2
    exit 1
  fi
done

echo "posthog_behavior_scope=ok"
