#!/usr/bin/env bash
set -euo pipefail

# R640 forwards only the authenticated AMD WSL peer to its local LAN through
# wg-aesthete. SNAT makes LAN replies return to R640 rather than ARP for WSL.
if [[ $(id -u) != 0 ]]; then
    echo 'run as root' >&2
    exit 1
fi
action=${1:-apply}
case "$action" in apply|rollback) ;; *) echo 'expected apply or rollback' >&2; exit 2 ;; esac

forward=(-i wg-aesthete -o eno2np1 -s 172.31.92.2/32 -d 192.168.88.0/24 -j ACCEPT)
return=(-i eno2np1 -o wg-aesthete -s 192.168.88.0/24 -d 172.31.92.2/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT)
snat=(-s 172.31.92.2/32 -d 192.168.88.0/24 -o eno2np1 -j SNAT --to-source 192.168.88.251)
old_forward=(-i wg-aesthete -o eno2np1 -s 172.31.92.2/32 -d 192.168.88.1/32 -p tcp --dport 22 -j ACCEPT)
old_return=(-i eno2np1 -o wg-aesthete -s 192.168.88.1/32 -d 172.31.92.2/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT)
old_snat=(-s 172.31.92.2/32 -d 192.168.88.1/32 -o eno2np1 -p tcp --dport 22 -j SNAT --to-source 192.168.88.251)

if [[ $action == apply ]]; then
    iptables -C FORWARD "${forward[@]}" 2>/dev/null || iptables -I FORWARD 1 "${forward[@]}"
    iptables -C FORWARD "${return[@]}" 2>/dev/null || iptables -I FORWARD 1 "${return[@]}"
    iptables -t nat -C POSTROUTING "${snat[@]}" 2>/dev/null || iptables -t nat -I POSTROUTING 1 "${snat[@]}"
    iptables -D FORWARD "${old_forward[@]}" 2>/dev/null || true
    iptables -D FORWARD "${old_return[@]}" 2>/dev/null || true
    iptables -t nat -D POSTROUTING "${old_snat[@]}" 2>/dev/null || true
else
    iptables -D FORWARD "${forward[@]}" 2>/dev/null || true
    iptables -D FORWARD "${return[@]}" 2>/dev/null || true
    iptables -t nat -D POSTROUTING "${snat[@]}" 2>/dev/null || true
    iptables -D FORWARD "${old_forward[@]}" 2>/dev/null || true
    iptables -D FORWARD "${old_return[@]}" 2>/dev/null || true
    iptables -t nat -D POSTROUTING "${old_snat[@]}" 2>/dev/null || true
fi
