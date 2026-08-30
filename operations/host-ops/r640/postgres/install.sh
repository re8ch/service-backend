#!/bin/bash
set -euo pipefail

source_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

install -o root -g root -m 0755 "$source_dir/re8ch-postgres-stack-start" /usr/local/sbin/re8ch-postgres-stack-start
install -o root -g root -m 0644 "$source_dir/re8ch-postgres-stack.service" /etc/systemd/system/re8ch-postgres-stack.service
install -o root -g root -m 0644 "$source_dir/re8ch-postgres-stack.timer" /etc/systemd/system/re8ch-postgres-stack.timer

for service in rbdmap patroni pgbouncer; do
  install -d -m 0755 "/etc/systemd/system/$service.service.d"
done
install -o root -g root -m 0644 "$source_dir/rbdmap.service.d/10-re8ch-timeouts.conf" /etc/systemd/system/rbdmap.service.d/10-re8ch-timeouts.conf
install -o root -g root -m 0644 "$source_dir/patroni.service.d/20-re8ch-nonblocking.conf" /etc/systemd/system/patroni.service.d/20-re8ch-nonblocking.conf
install -o root -g root -m 0644 "$source_dir/pgbouncer.service.d/20-re8ch-after-postgres.conf" /etc/systemd/system/pgbouncer.service.d/20-re8ch-after-postgres.conf

if [[ -e /etc/systemd/system/patroni.service.d/30-pigsty-rbd-mount.conf ]]; then
  unlink /etc/systemd/system/patroni.service.d/30-pigsty-rbd-mount.conf
fi

install -o root -g root -m 0644 /etc/fstab /etc/fstab.before-re8ch-postgres-nonblocking
replacement=$(grep -v '^[[:space:]]*#' "$source_dir/fstab.pigsty" | grep pigsty-r640)
awk -v replacement="$replacement" '
  /[[:space:]]\/data\/pigsty[[:space:]]/ {if (!done) print replacement; done=1; next}
  {print}
  END {if (!done) print replacement}
' /etc/fstab > /etc/fstab.re8ch-new
install -o root -g root -m 0644 /etc/fstab.re8ch-new /etc/fstab
unlink /etc/fstab.re8ch-new

systemctl daemon-reload
systemctl disable rbdmap.service patroni.service pgbouncer.service || true
systemctl enable re8ch-postgres-stack.timer
systemd-analyze verify re8ch-postgres-stack.service re8ch-postgres-stack.timer patroni.service pgbouncer.service
findmnt --verify
