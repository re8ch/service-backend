#!/usr/bin/env bash
set -euo pipefail

state_dir=/var/lib/re8ch-mubao-watchdog
mkdir -p "$state_dir"
exec 9>"$state_dir/lock"
flock -n 9 || exit 0

if ! systemctl is-active --quiet ssh; then
  systemctl restart ssh
fi

if ! /usr/local/sbin/re8ch-k3s-start-gate; then
  logger -t re8ch-mubao-watchdog 'resource or transit gate failed; no restart attempted'
  exit 0
fi

if curl --noproxy '*' -kfsS --max-time 8 https://127.0.0.1:6443/readyz 2>/dev/null | grep -qx ok; then
  rm -f "$state_dir/failures"
  exit 0
fi

failures=0
[[ ! -s "$state_dir/failures" ]] || read -r failures < "$state_dir/failures"
failures=$((failures + 1))
printf '%s\n' "$failures" > "$state_dir/failures"
(( failures >= 4 )) || exit 0

# A cluster-wide outage must not cause every server to restart together.
# Bind the accelerated identity: R640 forwards only that scoped source. Both
# peers remain valid after R640 becomes an agent in a later migration.
peer_healthy=false
for peer in 10.250.0.147 10.250.0.125; do
  if curl --interface 10.250.0.29 --noproxy '*' -kfsS --max-time 8 "https://${peer}:6443/readyz" 2>/dev/null | grep -qx ok; then
    peer_healthy=true
    break
  fi
done
if [[ "$peer_healthy" != true ]]; then
  logger -t re8ch-mubao-watchdog 'both peer APIs unready; holding local K3s'
  exit 0
fi

now=$(date +%s)
last=0
[[ ! -s "$state_dir/last-restart" ]] || read -r last < "$state_dir/last-restart"
(( now - last >= 1800 )) || exit 0
printf '%s\n' "$now" > "$state_dir/last-restart"
logger -t re8ch-mubao-watchdog 'local API unready for four checks; restarting K3s once'
systemctl restart k3s
