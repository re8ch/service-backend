#!/bin/sh
set -eu

systemctl is-active --quiet k3s.service
systemctl is-active --quiet ssh.service
ip -4 addr show | grep -q '192.0.2.208/'

directory=/mnt/c/ProgramData/RE8CH
test -d "$directory"
temporary="$directory/control-plane-wsl-heartbeat.json.tmp"
printf '{"nodeName":"restricted-node-296f36f2fb","unixSeconds":%s}\n' "$(date -u +%s)" > "$temporary"
mv -f "$temporary" "$directory/control-plane-wsl-heartbeat.json"
