#!/usr/bin/env bash

sanitize_registry_component() {
  local raw="${1:-}"
  raw="$(printf '%s' "${raw}" | tr '[:upper:]' '[:lower:]')"
  raw="$(printf '%s' "${raw}" | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//')"
  printf '%s' "${raw}"
}

infer_registry_project() {
  local function_name="${1:-}"
  local explicit="${REGISTRY_PROJECT:-${HARBOR_PROJECT:-${SAAS_TENANT:-${TENANT:-}}}}"

  if [[ -n "${explicit}" ]]; then
    sanitize_registry_component "${explicit}"
    return
  fi

  case "${function_name}" in
    qianwen-*|nuclio-qianwen-*) printf 'qianwen' ;;
    cardcat-*|nuclio-cardcat-*) printf 'cardcat' ;;
    ledger-*|nuclio-ledger-*) printf 'ledger' ;;
    anycam-*|nuclio-anycam-*) printf 'anycam' ;;
    bench-*|nuclio-bench-*) printf 'bench' ;;
    *) printf 'functions-shared' ;;
  esac
}

tenant_registry_for_function() {
  local function_name="${1:-}"
  local host="${REGISTRY_HOST:-registry.re8ch.com}"
  local project
  project="$(infer_registry_project "${function_name}")"
  printf '%s/%s' "${host}" "${project}"
}
