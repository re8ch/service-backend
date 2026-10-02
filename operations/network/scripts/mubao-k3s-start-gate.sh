#!/usr/bin/env bash
set -euo pipefail

# The ext4 VHD reports its maximum size. /mnt/m reports the actual Windows
# volume that contains Ubuntu's VHD and WSL swap file.
available_kib=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
(( available_kib >= 1572864 )) || { echo 'MUBAO gate: <1.5 GiB available RAM' >&2; exit 1; }

for path in / /mnt/m; do
  [[ -d "$path" ]] || { echo "MUBAO gate: $path is not mounted" >&2; exit 1; }
  available_kib=$(df -Pk "$path" | awk 'END {print $4}')
  (( available_kib >= 15728640 )) || { echo "MUBAO gate: $path has <15 GiB free" >&2; exit 1; }
done

ip -4 addr show dev wg-pt-mubao | grep -q '172\.31\.95\.30/' || {
  echo 'MUBAO gate: WireGuard transport address absent' >&2; exit 1;
}
exit 0
