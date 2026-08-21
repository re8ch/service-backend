#!/usr/bin/env bash
set -euo pipefail

mesh_iface="${MESH_INTERFACE:-wg-public-mesh}"
expected_peer="${1:-}"

if [[ -z "${expected_peer}" ]]; then
  echo "usage: $0 <peer-zt-ip>" >&2
  exit 2
fi

case "${expected_peer}" in
  192.0.2.192|192.0.2.178|192.0.2.112|192.0.2.182|192.0.2.74|192.0.2.138|192.0.2.89) ;;
  *) echo "peer is not in the seven-node experiment: ${expected_peer}" >&2; exit 2 ;;
esac

echo "## WireGuard"
wg show "${mesh_iface}" latest-handshakes

echo "## Selected route"
ip route get "${expected_peer}"

echo "## BGP summary"
vtysh -c 'show bgp ipv4 unicast summary'

echo "## BFD peers"
vtysh -c 'show bfd peers'

echo "## Peer reachability"
ping -c 3 -W 2 "${expected_peer}"

route_line="$(ip route get "${expected_peer}" | head -n 1)"
if [[ "${route_line}" != *"dev ${mesh_iface}"* ]]; then
  echo "FAIL: public-mesh /32 is not selected: ${route_line}" >&2
  exit 1
fi

echo "PASS: ${expected_peer} currently selects ${mesh_iface}"
