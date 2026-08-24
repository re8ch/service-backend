#!/bin/sh
set -eu

ca_dir=/var/lib/re8ch-ssh-ca
ca_key=$ca_dir/ssh_user_ca
ca_pub=/etc/ssh/re8ch_user_ca.pub
account=re8ch-ai
libexec=/usr/local/libexec/re8ch-ai

require_root() {
  [ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
}

ca_init() {
  require_root
  install -d -m 0700 -o root -g root "$ca_dir"
  if [ ! -f "$ca_key" ]; then
    ssh-keygen -q -t ed25519 -N '' -C re8ch-ssh-user-ca -f "$ca_key"
  fi
  chmod 0400 "$ca_key"
  chmod 0444 "$ca_key.pub"
  echo "CA public key: $ca_key.pub"
}

node_enroll() {
  require_root
  source_pub=${1:?CA public key path is required}
  id "$account" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$account"
  install -m 0444 -o root -g root "$source_pub" "$ca_pub"
  install -d -m 0755 -o root -g root /etc/ssh/auth_principals
  # This account is intentionally operation-only. Observation stays in the
  # sanitized Kubernetes MCP and does not create a second host access path.
  printf '%s\n' re8ch-ai-operate >"/etc/ssh/auth_principals/$account"
  chown root:root "/etc/ssh/auth_principals/$account"
  chmod 0444 "/etc/ssh/auth_principals/$account"
  install -d -m 0755 -o root -g root /etc/ssh/sshd_config.d
  printf '%s\n' \
    "TrustedUserCAKeys $ca_pub" \
    "AuthorizedPrincipalsFile /etc/ssh/auth_principals/%u" \
    > /etc/ssh/sshd_config.d/60-re8ch-user-ca.conf
  install -d -m 0755 -o root -g root "$libexec"
  install -m 0755 -o root -g root "$(dirname "$0")/re8ch-host-op" "$libexec/re8ch-host-op"
  printf '%s\n' "$account ALL=(root) NOPASSWD: $libexec/re8ch-host-op *" >/etc/sudoers.d/re8ch-ai-host-ops
  chmod 0440 /etc/sudoers.d/re8ch-ai-host-ops
  visudo -cf /etc/sudoers.d/re8ch-ai-host-ops
  sshd -t
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
}

case ${1:-} in
  ca-init) ca_init ;;
  node-enroll) shift; node_enroll "$@" ;;
  *) echo "usage: $0 {ca-init|node-enroll CA_PUBLIC_KEY}" >&2; exit 2 ;;
esac
