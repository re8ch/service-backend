#!/usr/bin/env bash
set -euo pipefail

# Permit the existing AMD WSL NFS client over its direct R640 WireGuard /32.
# Keep the original ZeroTier export in aesthete-amdwsl.exports for fallback.
if [[ $(id -u) != 0 ]]; then
    echo 'run as root' >&2
    exit 1
fi
target=/etc/exports.d/aesthete-amdwsl-wg.exports
desired='/srv/nas/shared 172.31.92.2(rw,sync,no_subtree_check,root_squash)'
if [[ -e $target && $(cat "$target") != "$desired" ]]; then
    echo "refusing to replace a different NFS export: $target" >&2
    exit 1
fi
if [[ ! -e $target ]]; then
    printf '%s\n' "$desired" > "$target"
    chmod 0644 "$target"
fi
exportfs -ra
exportfs -v | grep -F '172.31.92.2'
