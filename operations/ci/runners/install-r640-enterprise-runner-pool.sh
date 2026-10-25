#!/usr/bin/env bash
set -euo pipefail

# Add a generic enterprise-scoped R640 pool without removing the existing
# repository-scoped service-backend pool. Run as root with a short-lived token:
#   RUNNER_REGISTRATION_TOKEN=... ./install-r640-enterprise-runner-pool.sh

: "${RUNNER_REGISTRATION_TOKEN:?set a short-lived enterprise runner registration token}"

pool_size="${R640_ENTERPRISE_RUNNER_POOL_SIZE:-8}"
runner_user="github-runner"
source_dir="/home/${runner_user}/actions-runner-service-backend"
base_dir="/home/${runner_user}/actions-runner-enterprise"
enterprise_url="https://github.com/enterprises/re8ch"
runner_prefix="r640-enterprise"

if [[ "${EUID}" -ne 0 ]]; then
  echo "run this installer as root" >&2
  exit 1
fi
if [[ ! "${pool_size}" =~ ^[0-9]+$ ]] || ((pool_size < 1 || pool_size > 16)); then
  echo "R640_ENTERPRISE_RUNNER_POOL_SIZE must be between 1 and 16" >&2
  exit 1
fi
if [[ ! -x "${source_dir}/config.sh" ]]; then
  echo "runner distribution is unavailable at ${source_dir}" >&2
  exit 1
fi

for required in rsync runuser systemctl; do
  command -v "${required}" >/dev/null || {
    echo "missing required command: ${required}" >&2
    exit 1
  }
done

install -d -o "${runner_user}" -g "${runner_user}" -m 0755 \
  "/home/${runner_user}/actions-toolcache"
if [[ -d "${source_dir}/_work/_tool" ]]; then
  runuser -u "${runner_user}" -- rsync -a --ignore-existing \
    "${source_dir}/_work/_tool/" "/home/${runner_user}/actions-toolcache/"
fi

for ((slot = 1; slot <= pool_size; slot++)); do
  printf -v suffix "%02d" "${slot}"
  runner_name="${runner_prefix}-${suffix}"
  runner_dir="${base_dir}-${suffix}"

  install -d -o "${runner_user}" -g "${runner_user}" -m 0750 "${runner_dir}"
  if [[ ! -f "${runner_dir}/.runner" ]]; then
    runuser -u "${runner_user}" -- rsync -a \
      --exclude=.credentials \
      --exclude=.credentials_rsaparams \
      --exclude=.runner \
      --exclude=.runner_migrated \
      --exclude=.service \
      --exclude=_diag \
      --exclude=_work \
      --exclude=.env \
      "${source_dir}/" "${runner_dir}/"
    runuser -u "${runner_user}" -- cp "${source_dir}/.env" "${runner_dir}/.env"
    runuser -u "${runner_user}" -- cp "${source_dir}/.path" "${runner_dir}/.path"
    # Variables intentionally expand inside the runner-user shell.
    # shellcheck disable=SC2016
    runuser -u "${runner_user}" -- env \
      RUNNER_REGISTRATION_TOKEN="${RUNNER_REGISTRATION_TOKEN}" \
      RUNNER_TARGET_DIR="${runner_dir}" \
      RUNNER_TARGET_NAME="${runner_name}" \
      RUNNER_ENTERPRISE_URL="${enterprise_url}" \
      bash -c '
        cd "${RUNNER_TARGET_DIR}"
        ./config.sh --unattended \
          --url "${RUNNER_ENTERPRISE_URL}" \
          --token "${RUNNER_REGISTRATION_TOKEN}" \
          --name "${RUNNER_TARGET_NAME}" \
          --labels r640,untrusted-build \
          --work _work \
          --replace \
          --disableupdate
      '
  fi

  grep -qxF "PIP_BREAK_SYSTEM_PACKAGES=1" "${runner_dir}/.env" || \
    printf '%s\n' "PIP_BREAK_SYSTEM_PACKAGES=1" >>"${runner_dir}/.env"
  grep -qxF "PIP_USER=1" "${runner_dir}/.env" || \
    printf '%s\n' "PIP_USER=1" >>"${runner_dir}/.env"
  grep -qxF "BUILDX_BUILDER=r640-mihomos" "${runner_dir}/.env" || \
    printf '%s\n' "BUILDX_BUILDER=r640-mihomos" >>"${runner_dir}/.env"
  grep -qxF "AGENT_TOOLSDIRECTORY=/home/${runner_user}/actions-toolcache" "${runner_dir}/.env" || \
    printf '%s\n' "AGENT_TOOLSDIRECTORY=/home/${runner_user}/actions-toolcache" >>"${runner_dir}/.env"
  if ! grep -qF "/home/${runner_user}/.local/bin" "${runner_dir}/.path"; then
    sed -i "1s|^|/home/${runner_user}/.local/bin:|" "${runner_dir}/.path"
  fi
  chown "${runner_user}:${runner_user}" "${runner_dir}/.env" "${runner_dir}/.path"

  if [[ ! -f "${runner_dir}/.service" ]]; then
    (cd "${runner_dir}" && ./svc.sh install "${runner_user}")
  fi
  (cd "${runner_dir}" && ./svc.sh start)
done

systemctl list-units --type=service --state=running \
  "actions.runner.enterprises-re8ch.${runner_prefix}-*.service" --no-pager
