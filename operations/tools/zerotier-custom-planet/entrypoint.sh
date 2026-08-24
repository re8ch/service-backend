#!/bin/sh
set -eu

ZT_HOME=${ZT_HOME:-/var/lib/zerotier-one}
ZT_PORT=${ZT_PORT:-9993}

mkdir -p "${ZT_HOME}/networks.d"
printf '%s\n' "${ZT_PORT}" > "${ZT_HOME}/zerotier-one.port"
chmod 0600 "${ZT_HOME}/zerotier-one.port"

if [ "${ZEROTIER_API_SECRET:-}" != "" ]; then
  printf '%s\n' "${ZEROTIER_API_SECRET}" > "${ZT_HOME}/authtoken.secret"
  chmod 0600 "${ZT_HOME}/authtoken.secret"
fi

if [ "${ZEROTIER_IDENTITY_PUBLIC:-}" != "" ]; then
  printf '%s\n' "${ZEROTIER_IDENTITY_PUBLIC}" > "${ZT_HOME}/identity.public"
  chmod 0644 "${ZT_HOME}/identity.public"
fi

if [ "${ZEROTIER_IDENTITY_SECRET:-}" != "" ]; then
  printf '%s\n' "${ZEROTIER_IDENTITY_SECRET}" > "${ZT_HOME}/identity.secret"
  chmod 0600 "${ZT_HOME}/identity.secret"
fi

if [ "$#" -eq 0 ] && [ "${DEFAULT_NETWORK_ID:-}" != "" ]; then
  set -- "${DEFAULT_NETWORK_ID}"
fi

for network_id in "$@"; do
  touch "${ZT_HOME}/networks.d/${network_id}.conf"
done

cleanup() {
  if [ -f "${ZT_HOME}/zerotier-one.pid" ]; then
    kill "$(cat "${ZT_HOME}/zerotier-one.pid")" 2>/dev/null || true
  fi
}
trap cleanup INT TERM

/usr/sbin/zerotier-one "${ZT_HOME}" &

while :; do
  if [ -f "${ZT_HOME}/zerotier-one.pid" ]; then
    pid=$(cat "${ZT_HOME}/zerotier-one.pid")
    if [ -n "${pid}" ] && [ -d "/proc/${pid}" ]; then
      break
    fi
  fi
  sleep 1
done

zerotier-cli -D"${ZT_HOME}" info || true

wait
