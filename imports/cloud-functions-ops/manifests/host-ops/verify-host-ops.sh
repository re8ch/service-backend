#!/bin/sh
set -eu

dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sh -n "$dir/install-ssh-user-ca.sh" "$dir/sign-ssh-user-key.sh" "$dir/re8ch-host-op"

if grep -Eq '(NOPASSWD:[[:space:]]+ALL|authorized_keys)' \
  "$dir/install-ssh-user-ca.sh" "$dir/sign-ssh-user-key.sh" "$dir/re8ch-host-op"; then
  echo "unsafe generic host access pattern found" >&2
  exit 1
fi
grep -q 're8ch-ai-operate' "$dir/install-ssh-user-ca.sh"
grep -q 'V -1m:+10m' "$dir/sign-ssh-user-key.sh"
echo "host operation policy verified"
