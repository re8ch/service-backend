#!/usr/bin/env bash
set -euo pipefail

# Run on R640 after wg-pt-mubao, wg-pt-a1 and wg-pt-b1 exist. UFW persists
# these scoped transit rules across reboot and skips an identical existing rule.
if [[ $(id -u) != 0 ]]; then
  echo 'Run as root on R640' >&2
  exit 1
fi
for interface in wg-pt-mubao wg-pt-a1 wg-pt-b1 cilium_host; do
  ip link show "$interface" >/dev/null
done

for peer in 'wg-pt-a1 10.250.0.147 A1' 'wg-pt-b1 10.250.0.125 B1'; do
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
      from 10.250.0.29 to "$destination" port "$port" proto tcp \
      comment "$comment"
  done
done

# Embedded etcd peers must connect in both directions. Allow only the
# accelerated identities of A1/B1 and MUBAO, regardless of relay ingress leg.
for ingress in wg-pt-a1 wg-pt-b1; do
  for source in 10.250.0.147 10.250.0.125; do
    for port in 2379 2380; do
      ufw route allow in on "$ingress" out on wg-pt-mubao \
        from "$source" to 10.250.0.29 port "$port" proto tcp \
        comment 'MUBAO etcd peer A1/B1'
    done
  done
done

# A1 and B1 must be able to use MUBAO as an API server, including while R640
# is stopped for its server-to-agent handoff. The rules above only cover
# MUBAO-originated API traffic; established replies do not admit new inbound
# connections from either WSL control plane.
ufw route allow in on wg-pt-a1 out on wg-pt-mubao \
  from 10.250.0.147 to 10.250.0.29 port 6443 proto tcp \
  comment 'MUBAO API inbound A1'
ufw route allow in on wg-pt-b1 out on wg-pt-mubao \
  from 10.250.0.125 to 10.250.0.29 port 6443 proto tcp \
  comment 'MUBAO API inbound B1'

# MUBAO can originate the guarded API VIP. Keep the relay port limited to
# cluster-facing ingress interfaces and the one API destination.
for ingress in wg-pt-a1 wg-pt-b1 wg-public-mesh eno2np1 cilium_host; do
  ufw route allow in on "$ingress" out on wg-pt-mubao \
    to 10.250.0.1 port 6443 proto tcp comment 'MUBAO API VIP relay'
done

# Both metrics-server replicas run with hostNetwork on A1/B1. A1 may enter
# R640 through either relay leg as its policy route changes. Keep the kubelet
# probe limited to the two control-plane sources and one MUBAO destination.
for ingress in wg-pt-a1 wg-pt-b1; do
  for source in 10.250.0.147 10.250.0.125; do
    ufw route allow in on "$ingress" out on wg-pt-mubao \
      from "$source" to 10.250.0.29 port 10250 proto tcp \
      comment 'MUBAO kubelet metrics A1/B1'
  done
done

ufw route allow in on wg-pt-mubao out on cilium_host \
  from 10.250.0.29 to 10.42.6.0/24 comment 'MUBAO host to R640 pods'
ufw route allow in on wg-pt-mubao out on cilium_host \
  from 10.42.5.0/24 to 10.42.6.0/24 comment 'MUBAO pods to R640 pods'
