#!/bin/sh
set -eu

systemctl is-active --quiet k3s.service
systemctl is-active --quiet ssh.service

# ZeroTier is a management and routing path, not proof that the local k3s
# control plane is dead. Publish its state for diagnosis without suppressing
# the control-plane heartbeat and provoking a destructive host reboot.
zerotier_ready=false
if ip -4 addr show | grep -q '192.0.2.208/'; then
  zerotier_ready=true
fi

directory=/mnt/c/ProgramData/RE8CH
test -d "$directory"
temporary="$directory/control-plane-wsl-heartbeat.json.tmp"
printf '{"nodeName":"restricted-node-296f36f2fb","unixSeconds":%s,"controlPlaneReady":true,"zerotierReady":%s}\n' \
  "$(date -u +%s)" "$zerotier_ready" > "$temporary"
mv -f "$temporary" "$directory/control-plane-wsl-heartbeat.json"
