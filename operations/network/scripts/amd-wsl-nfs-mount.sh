#!/usr/bin/env bash
set -euo pipefail

# The existing fstab entry is noauto so WSL/K3s boot never waits for R640.
# Mount the shared data path after networking recovers, without changing fstab.
target=/Volumes/R640-NAS
if mountpoint -q "$target"; then
    exit 0
fi
timeout 30 mount "$target"
