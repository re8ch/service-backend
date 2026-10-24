#!/usr/bin/env bash
set -euo pipefail

# Expand the existing repository runner into independent service instances.
# Run as root on restricted-node-6dc3f140f9 and pass a short-lived repository registration token:
#   RUNNER_REGISTRATION_TOKEN=... ./install-r640-runner-pool.sh

: "${RUNNER_REGISTRATION_TOKEN:?set a short-lived GitHub runner registration token}"

pool_size="${R640_RUNNER_POOL_SIZE:-8}"
runner_user="github-runner"
base_dir="/home/${runner_user}/actions-runner-service-backend"
repository_url="https://github.com/re8ch/service-backend"
helm_version="v4.2.3"
helm_sha256="e9b88b4ee95b18c706839c28d3a0220e5bc470e9cd9262410c90793c45ff8b7c"
tool_tmp=""

cleanup() {
  if [[ -n "${tool_tmp}" && -d "${tool_tmp}" ]]; then
    rm -rf -- "${tool_tmp}"
  fi
}
trap cleanup EXIT

if [[ "${EUID}" -ne 0 ]]; then
  echo "run this installer as root" >&2
  exit 1
fi
if [[ ! "${pool_size}" =~ ^[0-9]+$ ]] || ((pool_size < 2 || pool_size > 16)); then
  echo "R640_RUNNER_POOL_SIZE must be between 2 and 16" >&2
  exit 1
fi
if [[ ! -f "${base_dir}/.runner" || ! -x "${base_dir}/config.sh" ]]; then
  echo "base runner is not configured at ${base_dir}" >&2
  exit 1
fi

for required in curl install jq mktemp rsync runuser sha256sum systemctl tar; do
  command -v "${required}" >/dev/null || {
    echo "missing required command: ${required}" >&2
    exit 1
  }
done

install_helm() {
  if command -v helm >/dev/null && \
    [[ "$(helm version --short)" == "${helm_version}+"* ]]; then
    return
  fi

  tool_tmp="$(mktemp -d)"
  curl --fail --silent --show-error --location \
    --proxy http://127.0.0.1:17891 \
    --output "${tool_tmp}/helm.tgz" \
    "https://get.helm.sh/helm-${helm_version}-linux-amd64.tar.gz"
  printf '%s  %s\n' "${helm_sha256}" "${tool_tmp}/helm.tgz" | sha256sum --check
  tar -xzf "${tool_tmp}/helm.tgz" -C "${tool_tmp}"
  install -o root -g root -m 0755 "${tool_tmp}/linux-amd64/helm" /usr/local/bin/helm
  rm -rf -- "${tool_tmp}"
  tool_tmp=""
}

install_helm

configure_python_environment() {
  local runner_dir="$1"
  grep -qxF "PIP_BREAK_SYSTEM_PACKAGES=1" "${runner_dir}/.env" || \
    printf '%s\n' "PIP_BREAK_SYSTEM_PACKAGES=1" >>"${runner_dir}/.env"
  grep -qxF "PIP_USER=1" "${runner_dir}/.env" || \
    printf '%s\n' "PIP_USER=1" >>"${runner_dir}/.env"
  grep -qxF "BUILDX_BUILDER=r640-mihomos" "${runner_dir}/.env" || \
    printf '%s\n' "BUILDX_BUILDER=r640-mihomos" >>"${runner_dir}/.env"
  if ! grep -qF "/home/${runner_user}/.local/bin" "${runner_dir}/.path"; then
    sed -i "1s|^|/home/${runner_user}/.local/bin:|" "${runner_dir}/.path"
  fi
  chown "${runner_user}:${runner_user}" "${runner_dir}/.env" "${runner_dir}/.path"
}

configure_python_environment "${base_dir}"

for ((slot = 2; slot <= pool_size; slot++)); do
  printf -v suffix "%02d" "${slot}"
  runner_name="r640-service-backend-${suffix}"
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
      "${base_dir}/" "${runner_dir}/"
    runuser -u "${runner_user}" -- cp "${base_dir}/.env" "${runner_dir}/.env"
    # Variables intentionally expand inside the runner-user shell.
    # shellcheck disable=SC2016
    runuser -u "${runner_user}" -- env \
      RUNNER_REGISTRATION_TOKEN="${RUNNER_REGISTRATION_TOKEN}" \
      RUNNER_TARGET_DIR="${runner_dir}" \
      RUNNER_TARGET_NAME="${runner_name}" \
      RUNNER_REPOSITORY_URL="${repository_url}" \
      bash -c '
        cd "${RUNNER_TARGET_DIR}"
        ./config.sh --unattended \
          --url "${RUNNER_REPOSITORY_URL}" \
          --token "${RUNNER_REGISTRATION_TOKEN}" \
          --name "${RUNNER_TARGET_NAME}" \
          --labels restricted-node-6dc3f140f9,untrusted-build \
          --work _work \
          --replace \
          --disableupdate
      '
  fi

  configure_python_environment "${runner_dir}"

  if [[ ! -f "${runner_dir}/.service" ]]; then
    (cd "${runner_dir}" && ./svc.sh install "${runner_user}")
  fi
  (cd "${runner_dir}" && ./svc.sh start)
done

systemctl list-units --type=service --state=running \
  "actions.runner.re8ch-service-backend.*" --no-pager

