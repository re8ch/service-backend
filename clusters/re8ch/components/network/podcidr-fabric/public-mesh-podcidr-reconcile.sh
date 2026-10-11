#!/bin/sh
# Reconcile only explicitly paired public-mesh PodCIDR BGP policies.
set -eu

host() { nsenter -t 1 -n chroot /host "$@"; }
loaded_revision="$(sha256sum /config/public-mesh.json /config/reconcile.sh)"
node="$(jq -c --arg name "$NODE_NAME" '.nodes[] | select(.name == $name)' /config/public-mesh.json)"
[ -n "$node" ] || exit 2
own_id="$(printf '%s' "$node" | jq -r .id)"
own_asn="$(printf '%s' "$node" | jq -r .asn)"
own_pod="$(printf '%s' "$node" | jq -r .podCIDR)"
[ "$own_pod" != null ] || exit 2

reconcile_peer() {
  peer_name="$1"
  peer="$(jq -c --arg name "$peer_name" '.nodes[] | select(.name == $name)' /config/public-mesh.json)"
  [ -n "$peer" ] || return 1
  printf '%s' "$peer" | jq -e --arg name "$NODE_NAME" '.podCIDRPeers | index($name) != null' >/dev/null
  peer_id="$(printf '%s' "$peer" | jq -r .id)"
  peer_ip="$(printf '%s' "$peer" | jq -r .transportIp)"
  peer_pod="$(printf '%s' "$peer" | jq -r .podCIDR)"
  [ "$peer_pod" != null ] || return 1
  # Never advertise a PodCIDR into a tunnel that cannot carry the return path.
  host wg show wg-public-mesh allowed-ips | awk -v transport="${peer_ip}/32" -v pod="$peer_pod" '
    { found_transport=0; found_pod=0; for(i=2;i<=NF;i++){if($i==transport)found_transport=1;if($i==pod)found_pod=1} if(found_transport && found_pod)ok=1 }
    END { exit !ok }' || return 1
  host vtysh -c 'show bgp ipv4 unicast summary json' | jq -e --arg ip "$peer_ip" '.peers[$ip].state == "Established"' >/dev/null || return 1

  config="$(host vtysh -c 'show running-config')"
  printf '%s\n' "$config" | grep -Fqx "  network $own_pod" || return 1
  inbound="FROM-$peer_id"
  outbound="RE8CH-POD-TO-$peer_id"
  # LA1 receives the API VIP as an additional R640 prefix.
  maximum=2
  if printf '%s' "$node" | jq -e --arg name "$peer_name" '.apiVipVia.peer == $name' >/dev/null; then
    maximum=3
  fi
  relay_vip="$(printf '%s' "$peer" | jq -r --arg name "$NODE_NAME" 'if .apiVipVia.peer == $name then .apiVipVia.prefix else empty end')"
  identity="10.250.0.$own_id/32"
  if ! printf '%s\n' "$config" | grep -Fqx "ip prefix-list $inbound seq 20 permit $peer_pod" &&
     ! printf '%s\n' "$config" | grep -Fqx "ip prefix-list $inbound seq 30 permit $peer_pod"; then
    sequence=20
    if printf '%s\n' "$config" | grep -Fq "ip prefix-list $inbound seq 20 "; then sequence=30; fi
    if printf '%s\n' "$config" | grep -Fq "ip prefix-list $inbound seq $sequence "; then return 1; fi
    host vtysh -c 'configure terminal' -c "ip prefix-list $inbound seq $sequence permit $peer_pod" -c end
  fi
  config="$(host vtysh -c 'show running-config')"
  if printf '%s\n' "$config" | grep -Fqx "ip prefix-list $outbound seq 10 permit $identity" &&
     printf '%s\n' "$config" | grep -Fqx "ip prefix-list $outbound seq 20 permit $own_pod" &&
     printf '%s\n' "$config" | grep -Fqx "  neighbor $peer_ip prefix-list $outbound out" &&
     printf '%s\n' "$config" | grep -Fqx "  neighbor $peer_ip maximum-prefix $maximum 90 restart 5"; then
    if [ -z "$relay_vip" ] || printf '%s\n' "$config" | grep -Fqx "ip prefix-list $outbound seq 30 permit $relay_vip"; then return 0; fi
  fi
  # The owned list is scoped to this one neighbor. Keep all other FRR policy.
  host vtysh -c 'configure terminal' -c "no ip prefix-list $outbound" -c "ip prefix-list $outbound seq 10 permit $identity" -c "ip prefix-list $outbound seq 20 permit $own_pod" -c end
  if [ -n "$relay_vip" ]; then
    host vtysh -c 'configure terminal' -c "ip prefix-list $outbound seq 30 permit $relay_vip" -c end
  fi
  host vtysh -c 'configure terminal' -c "router bgp $own_asn" -c 'address-family ipv4 unicast' -c "neighbor $peer_ip maximum-prefix $maximum 90 restart 5" -c "neighbor $peer_ip prefix-list $outbound out" -c end
  host vtysh -c "clear ip bgp $peer_ip soft"
  host vtysh -c 'write memory'
}

while :; do
  if [ "$loaded_revision" != "$(sha256sum /config/public-mesh.json /config/reconcile.sh)" ]; then
    exec /bin/sh /config/reconcile.sh
  fi
  printf '%s' "$node" | jq -r '.podCIDRPeers[]' | while read -r peer_name; do
    reconcile_peer "$peer_name" || echo "$NODE_NAME: public-mesh PodCIDR peer $peer_name not yet ready" >&2
  done
  sleep 30
done
