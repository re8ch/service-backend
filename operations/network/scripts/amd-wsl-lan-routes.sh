#!/usr/bin/env bash
set -euo pipefail

# Keep AMD WSL LAN traffic on two independent WireGuard paths. Do not change
# Kubernetes InternalIP, PodCIDR, control-plane or default routes.
exec 9>/run/re8ch-amd-wsl-lan-routes.lock
flock -n 9 || exit 0

lan_prefix=192.0.2.18/24
router_ip=192.0.2.240
r640_ip=192.0.2.169
direct_interface=wg-aesthete
direct_peer=kJUpAYZZ3VD5Gvmi0fYOee1ZhVRTCKDpd/veMc8n2B0=
direct_source=192.0.2.131
host_gateway=$(ip -4 route show default dev eth0 | awk '$1 == "default" && $2 == "via" {print $3; exit}')

# restricted-node-6dc3f140f9/NAS data and RouterOS management take the direct, lower-latency WSL
# WireGuard tunnel through restricted-node-6dc3f140f9. Keep the router route only when TCP 22 passes.
# Keep peer AllowedIPs in sync after a wg-quick restart. The less-specific LAN route
# below supplies an independent Windows-host fallback if this link fails.
if ip link show "$direct_interface" >/dev/null 2>&1 \
    && ping -I "$direct_interface" -c 1 -W 2 192.0.2.177 >/dev/null 2>&1; then
    wg set "$direct_interface" peer "$direct_peer" \
        allowed-ips 192.0.2.177/32,"$router_ip"/32,"$r640_ip"/32
    ip route replace "$r640_ip"/32 dev "$direct_interface" \
        src "$direct_source" metric 100
    ip route replace "$router_ip"/32 dev "$direct_interface" \
        src "$direct_source" metric 110
    if ! timeout 4 bash -c 'exec 3<>/dev/tcp/192.0.2.240/22' 2>/dev/null; then
        ip route del "$router_ip"/32 dev "$direct_interface" metric 110 2>/dev/null || true
    fi
else
    ip route del "$r640_ip"/32 dev "$direct_interface" metric 100 2>/dev/null || true
    ip route del "$router_ip"/32 dev "$direct_interface" metric 110 2>/dev/null || true
    if ip link show "$direct_interface" >/dev/null 2>&1; then
        wg set "$direct_interface" peer "$direct_peer" allowed-ips 192.0.2.177/32
    fi
fi

# Windows NAT forwards WSL traffic through the existing re8ch-amdhost tunnel
# to MikroTik. Probe only the router management address before installing the
# broader LAN route. The pre-existing ZeroTier /24 (metric 5000) remains the
# final fallback when the Windows-host path is unavailable.
if [[ -n "$host_gateway" ]]; then
    ip route replace "$router_ip"/32 via "$host_gateway" dev eth0 metric 50
    if timeout 4 bash -c 'exec 3<>/dev/tcp/192.0.2.240/22' 2>/dev/null; then
        ip route replace "$lan_prefix" via "$host_gateway" dev eth0 metric 1000
    else
        ip route del "$lan_prefix" via "$host_gateway" dev eth0 metric 1000 2>/dev/null || true
    fi
    ip route del "$router_ip"/32 via "$host_gateway" dev eth0 metric 50 2>/dev/null || true
fi
