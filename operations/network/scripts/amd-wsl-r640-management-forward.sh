#!/usr/bin/env bash
set -euo pipefail

# restricted-node-6dc3f140f9 forwards only AMD WSL management TCP/22 to MikroTik through the
# established wg-aesthete peer. SNAT makes the LAN reply return to restricted-node-6dc3f140f9.
if [[ $(id -u) != 0 ]]; then
    echo 'run as root' >&2
    exit 1
fi
action=${1:-apply}
case "$action" in apply|rollback) ;; *) echo 'expected apply or rollback' >&2; exit 2 ;; esac

forward=(-i wg-aesthete -o eno2np1 -s 192.0.2.131/32 -d 192.0.2.240/32 -p tcp --dport 22 -j ACCEPT)
return=(-i eno2np1 -o wg-aesthete -s 192.0.2.240/32 -d 192.0.2.131/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT)
snat=(-s 192.0.2.131/32 -d 192.0.2.240/32 -o eno2np1 -p tcp --dport 22 -j SNAT --to-source 192.0.2.169)

if [[ $action == apply ]]; then
    iptables -C FORWARD "${forward[@]}" 2>/dev/null || iptables -I FORWARD 1 "${forward[@]}"
    iptables -C FORWARD "${return[@]}" 2>/dev/null || iptables -I FORWARD 1 "${return[@]}"
    iptables -t nat -C POSTROUTING "${snat[@]}" 2>/dev/null || iptables -t nat -I POSTROUTING 1 "${snat[@]}"
else
    iptables -D FORWARD "${forward[@]}" 2>/dev/null || true
    iptables -D FORWARD "${return[@]}" 2>/dev/null || true
    iptables -t nat -D POSTROUTING "${snat[@]}" 2>/dev/null || true
fi
