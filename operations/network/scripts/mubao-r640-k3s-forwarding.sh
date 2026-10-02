#!/usr/bin/env bash
set -euo pipefail

# Run on restricted-node-6dc3f140f9 after wg-pt-mubao, wg-pt-a1 and wg-pt-b1 exist. UFW persists
# these scoped transit rules across reboot and skips an identical existing rule.
if [[ $(id -u) != 0 ]]; then
  echo 'Run as root on restricted-node-6dc3f140f9' >&2
  exit 1
fi
for interface in wg-pt-mubao wg-pt-a1 wg-pt-b1 cilium_host; do
  ip link show "$interface" >/dev/null
done

for peer in 'wg-pt-a1 192.0.2.173 A1' 'wg-pt-b1 192.0.2.213 B1'; do
  read -r interface destination name <<< "$peer"
  for port in 6443 2379 2380 10251; do
    if [[ $port == 6443 ]]; then
      comment="MUBAO K3s API via $name"
    elif [[ $port == 10251 ]]; then
      comment="MUBAO aggregated metrics API via $name"
    else
      comment="MUBAO K3s etcd client $name"
    fi
    ufw route allow in on wg-pt-mubao out on "$interface" \
      from 192.0.2.114 to "$destination" port "$port" proto tcp \
      comment "$comment"
  done
done

# Embedded etcd peers must connect in both directions. Allow only the
# accelerated identities of A1/B1 and MUBAO, regardless of relay ingress leg.
for ingress in wg-pt-a1 wg-pt-b1; do
  for source in 192.0.2.173 192.0.2.213; do
    for port in 2379 2380; do
      ufw route allow in on "$ingress" out on wg-pt-mubao \
        from "$source" to 192.0.2.114 port "$port" proto tcp \
        comment 'MUBAO etcd peer A1/B1'
    done
  done
done

# Both metrics-server replicas run with hostNetwork on A1/B1. A1 may enter
# restricted-node-6dc3f140f9 through either relay leg as its policy route changes. Keep the kubelet
# probe limited to the two control-plane sources and one MUBAO destination.
for ingress in wg-pt-a1 wg-pt-b1; do
  for source in 192.0.2.173 192.0.2.213; do
    ufw route allow in on "$ingress" out on wg-pt-mubao \
      from "$source" to 192.0.2.114 port 10250 proto tcp \
      comment 'MUBAO kubelet metrics A1/B1'
  done
done

ufw route allow in on wg-pt-mubao out on cilium_host \
  from 192.0.2.114 to 192.0.2.18/24 comment 'MUBAO host to restricted-node-6dc3f140f9 pods'
ufw route allow in on wg-pt-mubao out on cilium_host \
  from 192.0.2.214/24 to 192.0.2.18/24 comment 'MUBAO pods to restricted-node-6dc3f140f9 pods'
