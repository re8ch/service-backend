#!/usr/bin/env bash

add_unique_key() {
  local key="$1"
  local existing
  [[ -z "${key}" ]] && return
  for existing in "${function_env_keys[@]:-}"; do
    [[ "${existing}" == "${key}" ]] && return
  done
  function_env_keys+=("${key}")
}

add_keys() {
  local key
  for key in "$@"; do
    add_unique_key "${key}"
  done
}

add_common_runtime_env() {
  add_keys NODE_ENV QIANWEN_DOMAIN FRONTEND_BASE_URL API_BASE_URL PUBLIC_API_BASE_URL
}

add_mgr_env() {
  add_keys MGR_RPC_BASE_URL MGR_RPC_RW_BASE_URL MGR_RPC_RO_BASE_URL PGRST_JWT_AUD
}

add_rpc_env() {
  add_common_runtime_env
  add_mgr_env
  add_keys PGRST_JWT_SECRET PGRST_DB_ANON_ROLE PGRST_DB_SCHEMAS
}

add_oss_env() {
  add_keys \
    OSSURL \
    ALIYUN_OSS_REGION ALIYUN_OSS_BUCKET ALIYUN_OSS_ACCESS_KEY_ID ALIYUN_OSS_ACCESS_KEY_SECRET ALIYUN_OSS_STS_BUCKETS \
    FILESYNC_OSS_BUCKET FILESYNC_OSS_REGION FILESYNC_CDN_BASE_URL FILESYNC_CDN_DOMAIN FILESYNC_OSS_SOURCE_DOMAIN \
    FILESYNC_OSS_ACCESS_KEY_ID FILESYNC_OSS_ACCESS_KEY_SECRET FILESYNC_LEGACY_SOURCE_DOMAINS
}

add_sts_env() {
  add_oss_env
  add_keys ALIYUN_STS_ROLE_ARN OSS_STS_ROLE_ARN ALIYUN_STS_POLICY ALIYUN_STS_OBJECT_PREFIX \
    ALIYUN_STS_DURATION_SECONDS ALIYUN_STS_ROLE_SESSION_NAME ALIYUN_STS_ENDPOINT
}

add_billing_env() {
  add_keys \
    ALIPAY_APP_ID ALIPAY_GATEWAY ALIPAY_FRONTEND_RETURN_URL ALIPAY_RETURN_URL ALIPAY_NOTIFY_URL \
    ALIPAY_PRIVATE_KEY ALIPAY_PUBLIC_KEY \
    WX_PAY_ENABLED WX_PAY_MCH_ID WX_PAY_APP_ID WX_PAY_SERIAL_NO WX_PAY_PRIVATE_KEY WX_PAY_API_V3_KEY \
    WX_PAY_PLATFORM_PUBLIC_KEY WX_PAY_PLATFORM_SERIAL_NO WX_PAY_GATEWAY WX_PAY_NOTIFY_URL
}

add_auth_env() {
  add_keys \
    FEISHU_APP_ID FEISHU_APP_SECRET FEISHU_REDIRECT_URI \
    WX_WEBAPP_ID WX_WEBAPP_SECRET WX_WEBAPP_REDIRECT_URI \
    WX_MINI_APPID WX_MINI_APP_ID WECHAT_MINI_APP_ID WX_MINI_APP_SECRET WX_MINI_APPSECRET WECHAT_MINI_APP_SECRET \
    ALIYUN_SMS_ACCESS_KEY_ID ALIYUN_SMS_ACCESS_KEY_SECRET ALIYUN_SMS_SIGN_NAME \
    ALIYUN_SMS_TEMPLATE_CODE_REGISTER ALIYUN_SMS_TEMPLATE_CODE_LOGIN ALIYUN_SMS_TEMPLATE_CODE \
    ALIYUN_SMS_REGION_ID ALIYUN_SMS_ENDPOINT PHONE_SMS_MOCK PHONE_SMS_EXPOSE_CODE
}

add_posthog_env() {
  add_keys POSTHOG_RELAY_ENABLED POSTHOG_PROXY_URL POSTHOG_PROJECT_TOKEN POSTHOG_HOST POSTHOG_RELAY_TIMEOUT_MS
}

add_qw_gateway_env() {
  add_keys \
    QW_ANALYTICS_URL QW_API_CORE_URL QW_ASSETS_API_URL QW_ASSETS_WORKFLOW_URL QW_IMAGE_API_URL \
    QW_INTEGRATIONS_API_URL QW_AUTH_WORKFLOW_URL QW_BILLING_WORKFLOW_URL QW_ADMIN_WORKFLOW_URL \
    QW_PUBLIC_ASSETS_WORKFLOW_URL QW_USER_WORKFLOW_URL QW_OSS_STS_URL QW_REVERSE_IMAGE_PROMPT_URL \
    QW_SCRIPT_API_URL QW_SCRIPT_WORKFLOW_URL QW_IMPORT_DOCX_URL QW_STORYBOARD_API_URL \
    QW_STORYBOARD_WORKFLOW_URL QW_TASKS_URL QW_TEXT_API_URL QW_VIDEO_META_URL \
    QW_VIDEO_WORKFLOW_URL QW_VIDEO_API_URL QW_WORKFLOW_API_URL
}

add_api_edge_env() {
  add_keys \
    API_ROOT_BASE_URL API_PUBLIC_BASE_URL API_REGION_NODES API_REGION_TTL_SECONDS API_REGION_CACHE_VERSION \
    API_EDGE_NAME API_EDGE_NODE_NAME API_EDGE_REGION API_EDGE_ZT_IP API_EDGE_CAPACITY_STATE
}

function_env_allowlist() {
  local suffix="$1"
  function_env_keys=()

  case "${suffix}" in
    analytics)
      add_common_runtime_env
      add_posthog_env
      ;;
    api-core|video-meta)
      add_rpc_env
      ;;
    api-gateway|edge-router)
      add_common_runtime_env
      add_mgr_env
      add_qw_gateway_env
      add_api_edge_env
      add_oss_env
      add_keys LISTEN_ADDR
      ;;
    image-api|tasks)
      add_rpc_env
      add_oss_env
      ;;
    image-worker)
      add_rpc_env
      add_oss_env
      add_keys WORKER_CLAIM_LIMIT
      ;;
    oss-sts)
      add_common_runtime_env
      add_sts_env
      ;;
    reverse-image-prompt)
      add_rpc_env
      add_oss_env
      ;;
    integrations-api)
      add_rpc_env
      add_oss_env
      add_auth_env
      add_billing_env
      add_keys TRANSFER_SECRET CROSS_BORDER_TRANSFER_URL
      ;;
    assets-api|script-api|storyboard-api|text-api|video-api|workflow-api)
      add_rpc_env
      add_oss_env
      add_auth_env
      add_billing_env
      ;;
    *)
      add_rpc_env
      ;;
  esac

  printf '%s\n' "${function_env_keys[@]}"
}

function_suffix_from_name() {
  local name="$1"
  name="${name#nuclio-}"
  name="${name#qianwen-prod-}"
  name="${name#qianwen-}"
  printf '%s\n' "${name}"
}
