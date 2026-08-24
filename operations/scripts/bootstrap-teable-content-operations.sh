#!/usr/bin/env bash
set -euo pipefail

: "${TEABLE_URL:?set TEABLE_URL, for example https://teable.re8ch.com}"
: "${TEABLE_BASE_ID:?set TEABLE_BASE_ID}"

cookie_file="$(mktemp)"
response_file="$(mktemp)"
trap 'unlink "$cookie_file"; unlink "$response_file"' EXIT

curl_args=(-sS -H "content-type: application/json")
if [[ -n "${TEABLE_TOKEN:-}" ]]; then
  curl_args+=(-H "authorization: Bearer ${TEABLE_TOKEN}")
else
  : "${TEABLE_EMAIL:?set TEABLE_EMAIL when TEABLE_TOKEN is not provided}"
  : "${TEABLE_PASSWORD:?set TEABLE_PASSWORD when TEABLE_TOKEN is not provided}"
  curl "${curl_args[@]}" --fail -c "$cookie_file" \
    --data "$(jq -nc --arg email "$TEABLE_EMAIL" --arg password "$TEABLE_PASSWORD" \
      '{email:$email,password:$password}')" \
    "$TEABLE_URL/api/auth/signin" >"$response_file"
  curl_args+=(-b "$cookie_file")
fi

list_tables() {
  curl "${curl_args[@]}" --fail "$TEABLE_URL/api/base/$TEABLE_BASE_ID/table"
}

table_id_by_name() {
  local name="$1"
  list_tables | jq -r --arg name "$name" '
    (if type == "array" then . else (.tables // []) end)
    | map(select(.name == $name)) | first | .id // empty
  '
}

ensure_table() {
  local name="$1"
  local payload="$2"
  local table_id
  table_id="$(table_id_by_name "$name")"
  if [[ -z "$table_id" ]]; then
    curl "${curl_args[@]}" --fail-with-body \
      --data "$payload" \
      "$TEABLE_URL/api/base/$TEABLE_BASE_ID/table/" >"$response_file"
    table_id="$(jq -r '.id' "$response_file")"
    printf 'created table %s (%s)\n' "$name" "$table_id" >&2
  else
    printf 'kept table %s (%s)\n' "$name" "$table_id" >&2
  fi
  printf '%s\n' "$table_id"
}

ensure_record() {
  local table_id="$1"
  local primary_field="$2"
  local primary_value="$3"
  local record_fields="$4"
  local record_id
  record_id="$(
    curl "${curl_args[@]}" --fail \
      "$TEABLE_URL/api/table/$table_id/record?take=1000&fieldKeyType=name" |
      jq -r --arg field "$primary_field" --arg value "$primary_value" \
        '.records | map(select(.fields[$field] == $value)) | first | .id // empty'
  )"
  if [[ -z "$record_id" ]]; then
    curl "${curl_args[@]}" --fail-with-body \
      --data "$(jq -nc --argjson fields "$record_fields" \
        '{fieldKeyType:"name",typecast:true,records:[{fields:$fields}]}')" \
      "$TEABLE_URL/api/table/$table_id/record" >"$response_file"
    record_id="$(jq -r '.records[0].id' "$response_file")"
    printf 'created record %s (%s)\n' "$primary_value" "$record_id" >&2
  else
    printf 'kept record %s (%s)\n' "$primary_value" "$record_id" >&2
  fi
}

content_task_payload="$(jq -nc '{
  name: "内容任务",
  description: "微信公众号选题、生产、审核与发布的主任务表",
  fields: [
    {name:"任务名称", type:"singleLineText"},
    {name:"任务编号", type:"singleLineText"},
    {name:"栏目", type:"singleLineText"},
    {name:"目标读者", type:"singleLineText"},
    {name:"主题", type:"longText"},
    {name:"状态", type:"singleLineText"},
    {name:"当前阶段", type:"singleLineText"},
    {name:"优先级", type:"singleLineText"},
    {name:"计划发布日期", type:"date"},
    {name:"负责人", type:"singleLineText"},
    {name:"Agent策略", type:"longText"},
    {name:"验收标准", type:"longText"},
    {name:"阻塞原因", type:"longText"},
    {name:"产物目录", type:"singleLineText"}
  ]
}')"

artifact_payload="$(jq -nc '{
  name: "图文产物",
  description: "任务生成的文章、封面、正文配图和审核版本",
  fields: [
    {name:"产物名称", type:"singleLineText"},
    {name:"任务编号", type:"singleLineText"},
    {name:"产物类型", type:"singleLineText"},
    {name:"状态", type:"singleLineText"},
    {name:"本地路径", type:"singleLineText"},
    {name:"版本", type:"singleLineText"},
    {name:"说明", type:"longText"}
  ]
}')"

agent_run_payload="$(jq -nc '{
  name: "Agent运行",
  description: "Kagent、Codex 和人工审核各阶段的可追溯运行记录",
  fields: [
    {name:"运行名称", type:"singleLineText"},
    {name:"运行编号", type:"singleLineText"},
    {name:"任务编号", type:"singleLineText"},
    {name:"Agent", type:"singleLineText"},
    {name:"阶段", type:"singleLineText"},
    {name:"状态", type:"singleLineText"},
    {name:"开始时间", type:"date"},
    {name:"结束时间", type:"date"},
    {name:"结果摘要", type:"longText"},
    {name:"错误", type:"longText"}
  ]
}')"

content_task_id="$(ensure_table "内容任务" "$content_task_payload")"
artifact_id="$(ensure_table "图文产物" "$artifact_payload")"
agent_run_id="$(ensure_table "Agent运行" "$agent_run_payload")"

ensure_record "$content_task_id" "任务编号" "WX-GNU-202607-001" "$(jq -nc '{
  "任务名称":"GnuCash 操作指南：月末结账检查清单",
  "任务编号":"WX-GNU-202607-001",
  "栏目":"GnuCash操作指南",
  "目标读者":"使用 GnuCash 管理个人或小微企业账务的中文读者",
  "主题":"用可复核的步骤完成月末结账、对账、备份和下月初始化",
  "状态":"待人工审核",
  "当前阶段":"图文包已生成",
  "优先级":"P1",
  "计划发布日期":"2026-07-31T02:00:00.000Z",
  "负责人":"内容运营",
  "Agent策略":"Codex 负责资料整理、文章结构、配图提示词与成稿；Kagent 负责领取任务、阶段编排和状态回写；发布前必须人工审核。",
  "验收标准":"正文适配微信公众号；步骤准确可复核；包含封面和正文配图；不编造 GnuCash 功能；发布前完成人工校对。",
  "阻塞原因":"等待人工审核；Kagent 模型 Secret 和 Codex 模型登录仍需人工配置。",
  "产物目录":"outputs/gnucash-month-end-20260717"
}')"

ensure_record "$artifact_id" "任务编号" "WX-GNU-202607-001" "$(jq -nc '{
  "产物名称":"GnuCash 月末结账图文包",
  "任务编号":"WX-GNU-202607-001",
  "产物类型":"微信公众号图文包",
  "状态":"待人工审核",
  "本地路径":"outputs/gnucash-month-end-20260717",
  "版本":"v1",
  "说明":"包含文章 Markdown、封面图、正文配图及发布清单。"
}')"

ensure_record "$agent_run_id" "运行编号" "RUN-WX-GNU-202607-001-BOOTSTRAP" "$(jq -nc '{
  "运行名称":"首期 GnuCash 图文包恢复登记",
  "运行编号":"RUN-WX-GNU-202607-001-BOOTSTRAP",
  "任务编号":"WX-GNU-202607-001",
  "Agent":"Codex",
  "阶段":"图文生成",
  "状态":"已完成",
  "开始时间":"2026-07-17T00:00:00.000Z",
  "结束时间":"2026-07-17T02:30:00.000Z",
  "结果摘要":"已生成文章、封面、正文配图和发布清单，并在平台恢复后补录运行记录。",
  "错误":""
}')"

jq -nc \
  --arg baseId "$TEABLE_BASE_ID" \
  --arg contentTaskTableId "$content_task_id" \
  --arg artifactTableId "$artifact_id" \
  --arg agentRunTableId "$agent_run_id" \
  '{
    baseId:$baseId,
    contentTaskTableId:$contentTaskTableId,
    artifactTableId:$artifactTableId,
    agentRunTableId:$agentRunTableId
  }'
