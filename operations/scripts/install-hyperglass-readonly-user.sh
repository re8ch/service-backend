#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || -z ${HYPERGLASS_PUBLIC_KEY:-} ]]; then
  echo "usage: HYPERGLASS_PUBLIC_KEY='ssh-ed25519 ...' $0 <ssh-host>" >&2
  exit 2
fi

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
host=$1
scp "$ROOT_DIR/operations/scripts/hyperglass-readonly-shell" "$host:/tmp/hyperglass-readonly-shell"
ssh "$host" "sudo bash -s -- '$HYPERGLASS_PUBLIC_KEY'" <<'REMOTE'
set -euo pipefail
HYPERGLASS_PUBLIC_KEY=$1
install -o root -g root -m 0755 /tmp/hyperglass-readonly-shell /usr/local/sbin/hyperglass-readonly-shell
if ! id hyperglass-ro >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/local/sbin/hyperglass-readonly-shell --groups frrvty hyperglass-ro
else
  usermod --shell /usr/local/sbin/hyperglass-readonly-shell --append --groups frrvty hyperglass-ro
fi
install -d -o hyperglass-ro -g hyperglass-ro -m 0700 /home/hyperglass-ro/.ssh
printf '%s\n' "$HYPERGLASS_PUBLIC_KEY" > /home/hyperglass-ro/.ssh/authorized_keys
chown hyperglass-ro:hyperglass-ro /home/hyperglass-ro/.ssh/authorized_keys
chmod 0600 /home/hyperglass-ro/.ssh/authorized_keys
REMOTE
