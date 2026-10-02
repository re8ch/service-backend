#!/usr/bin/env bash
set -euo pipefail

# MUBAO's Windows host initiates this independent R640 transit over RouterOS
# WAN DNAT. This stage establishes transport only; AdvFab routes are separate.
interface=wg-pt-mubao
port=51893
key_file=/etc/wireguard/${interface}.key
config_file=/etc/wireguard/${interface}.conf
host_public_key='gmENnp/ccyPP1PoNGVniEHhgq/OO67I/VAwiMCfsXWE='
wsl_public_key='VVuXaxa2ZsblzpqhbzrjjZk/F22ilnz6QeL0DdYDlwA='

if [[ $(id -u) != 0 ]]; then
  echo 'Run as root on R640' >&2
  exit 1
fi
if [[ -e $config_file || -e $key_file ]] || ip link show "$interface" >/dev/null 2>&1; then
  echo 'R640 MUBAO tunnel already exists; inspect before changing it' >&2
  exit 1
fi
if ss -H -lun "sport = :$port" | grep -q .; then
  echo "UDP $port already in use" >&2
  exit 1
fi

umask 077
wg genkey > "$key_file"
cat > "$config_file" <<EOF
[Interface]
Address = 172.31.95.128/32
ListenPort = $port
MTU = 1200
Table = off
PostUp = wg set %i private-key $key_file
PostUp = ip route replace 172.31.95.29/32 dev %i scope link
PostUp = ip route replace 172.31.95.30/32 dev %i scope link
PostUp = ip route replace 10.250.0.29/32 dev %i scope link
PostDown = ip route del 172.31.95.29/32 dev %i || true
PostDown = ip route del 172.31.95.30/32 dev %i || true
PostDown = ip route del 10.250.0.29/32 dev %i || true

[Peer]
# MUBAO Windows host management path.
PublicKey = $host_public_key
AllowedIPs = 172.31.95.29/32

[Peer]
# MUBAO WSL server path; AdvFab identity remains separate from ZeroTier.
PublicKey = $wsl_public_key
AllowedIPs = 172.31.95.30/32, 10.250.0.29/32, 10.42.5.0/24, 10.250.0.1/32
EOF
chmod 600 "$key_file" "$config_file"
ufw allow in on eno2np1 to 192.168.88.251 port "$port" proto udp comment 'RE8CH MUBAO WG transit'
systemctl enable --now "wg-quick@${interface}.service"
wg show "$interface" public-key
