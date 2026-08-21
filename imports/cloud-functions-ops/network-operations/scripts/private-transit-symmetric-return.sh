#!/bin/sh
set -eu

case "${1:-}" in
  start)
    nft delete table inet re8ch_private_transit 2>/dev/null || true
    nft -f /etc/re8ch/private-transit-symmetric-return.nft
    ip route replace 192.0.2.106/24 dev wg-pt-a1 table 15931
    ip route replace 192.0.2.106/24 dev wg-pt-b1 table 15932
    ip rule del priority 10901 2>/dev/null || true
    ip rule del priority 10902 2>/dev/null || true
    ip rule add priority 10901 fwmark 0x1 lookup 15931
    ip rule add priority 10902 fwmark 0x2 lookup 15932
    ;;
  stop)
    ip rule del priority 10901 2>/dev/null || true
    ip rule del priority 10902 2>/dev/null || true
    ip route flush table 15931
    ip route flush table 15932
    nft delete table inet re8ch_private_transit 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 2
    ;;
esac
