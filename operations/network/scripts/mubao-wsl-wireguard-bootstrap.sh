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
Address = 172.31.95.30/32
MTU = 1200
Table = off
PostUp = wg set %i private-key $key
PostUp = ip address show dev lo | grep -q '10.250.0.29/32' || ip address add 10.250.0.29/32 dev lo
PostUp = ip route replace 172.31.95.128/32 dev %i src 172.31.95.30
PostUp = ip route replace 10.250.0.128/32 dev %i src 10.250.0.29
PostDown = ip route del 172.31.95.128/32 dev %i || true
PostDown = ip route del 10.250.0.128/32 dev %i || true

[Peer]
PublicKey = H6trdN+xorHMYEFsPV6G8CMLmhMZkObgnQfcXYUSNVQ=
AllowedIPs = 172.31.95.128/32, 10.250.0.128/32, 10.250.0.1/32, 10.250.0.147/32, 10.250.0.125/32, 10.42.0.0/16
Endpoint = 223.159.210.222:51896
PersistentKeepalive = 15
EOF
chmod 600 "$config"
systemctl enable --now "wg-quick@${interface}.service"
wg show "$interface" public-key
