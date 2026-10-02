#!/usr/bin/env bash
set -euo pipefail

interface=wg-pt-mubao
config=/etc/wireguard/${interface}.conf
key=/etc/wireguard/${interface}.key

if [[ $(id -u) != 0 ]]; then
  echo 'Run as root inside MUBAO Ubuntu' >&2
  exit 1
fi
if [[ ! -s $key ]]; then
  echo "Missing WSL private key: $key" >&2
  exit 1
fi
if [[ -e $config ]] || ip link show "$interface" >/dev/null 2>&1; then
  echo 'MUBAO WSL tunnel already exists; inspect before changing it' >&2
  exit 1
fi

umask 077
cat > "$config" <<EOF
[Interface]
Address = 192.0.2.246/32
MTU = 1200
Table = off
PostUp = wg set %i private-key $key
PostUp = ip address show dev lo | grep -q '192.0.2.114/32' || ip address add 192.0.2.114/32 dev lo
PostUp = ip route replace 192.0.2.111/32 dev %i src 192.0.2.246
PostUp = ip route replace 192.0.2.205/32 dev %i src 192.0.2.114
PostDown = ip route del 192.0.2.111/32 dev %i || true
PostDown = ip route del 192.0.2.205/32 dev %i || true

[Peer]
PublicKey = H6trdN+xorHMYEFsPV6G8CMLmhMZkObgnQfcXYUSNVQ=
AllowedIPs = 192.0.2.111/32, 192.0.2.205/32, 192.0.2.173/32, 192.0.2.213/32, 192.0.2.224/16
Endpoint = 192.0.2.79:51893
PersistentKeepalive = 15
EOF
chmod 600 "$config"
systemctl enable --now "wg-quick@${interface}.service"
wg show "$interface" public-key
