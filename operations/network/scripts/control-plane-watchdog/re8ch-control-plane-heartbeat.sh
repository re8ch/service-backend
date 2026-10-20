#!/bin/sh
set -eu

: "${RE8CH_NODE_NAME:?missing RE8CH_NODE_NAME}"
: "${RE8CH_WATCHDOG_ROLE:?missing RE8CH_WATCHDOG_ROLE}"
: "${RE8CH_K3S_SERVICE:?missing RE8CH_K3S_SERVICE}"
: "${RE8CH_NODE_IDENTITY:?missing RE8CH_NODE_IDENTITY}"

systemctl is-active --quiet "$RE8CH_K3S_SERVICE"
systemctl is-active --quiet ssh.service

# ZeroTier is a management and routing path, not proof that the local k3s
# control plane is dead. Publish its state for diagnosis without suppressing
# the control-plane heartbeat and provoking a destructive host reboot.
zerotier_ready=false
if ip -4 addr show | grep -Fq "$RE8CH_NODE_IDENTITY/"; then
  zerotier_ready=true
fi

directory=/mnt/c/ProgramData/RE8CH
test -d "$directory"
temporary="$directory/control-plane-wsl-heartbeat.json.tmp"
printf '{"nodeName":"%s","role":"%s","unixSeconds":%s,"serviceReady":true,"zerotierReady":%s}\n' \
  "$RE8CH_NODE_NAME" "$RE8CH_WATCHDOG_ROLE" "$(date -u +%s)" "$zerotier_ready" > "$temporary"
mv -f "$temporary" "$directory/control-plane-wsl-heartbeat.json"
