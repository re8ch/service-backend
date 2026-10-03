#!/bin/sh
set -eu
host() { nsenter -t 1 -n chroot /host "$@"; }
root=/host/var/lib/re8ch/host-network
lib=/host/usr/local/lib/re8ch-host-network
mkdir -p "$root" "$lib/checks"
chmod 700 "$root" "$lib" "$lib/checks"
cp /var/run/secrets/kubernetes.io/serviceaccount/ca.crt "$root/kube-ca.crt"
host test -x /usr/bin/curl
# This single-node pilot cannot cause concurrent fleet mutations.
jq -e --arg node "$NODE_NAME" '.node == $node' /config/package.json >/dev/null
# Install recovery before enabling any forward reconciliation.
for file in agent.py; do
  cp "/config/$file" "$lib/$file.next"
  chmod 700 "$lib/$file.next"
  mv "$lib/$file.next" "$lib/$file"
done
cp /config/health.py "$lib/checks/fabric.next"
chmod 700 "$lib/checks/fabric.next"
mv "$lib/checks/fabric.next" "$lib/checks/fabric"
for file in /config/*.service /config/*.timer; do
  cp "$file" "/host/etc/systemd/system/$(basename "$file")"
done
host systemctl daemon-reload
host systemctl enable --now re8ch-host-network-watchdog.timer
host systemctl start re8ch-host-network-recover.service
# Wait for the reconciler binary that honors ownership handoff. A config
# mount alone does not prove its already-running shell has loaded the change.
if [ "$(jq -r .scope /config/package.json)" = control-transit-peer ]; then
  # Existing owner keeps routes but yields this peer to the transactional agent.
  host systemctl is-active --quiet re8ch-control-pod-mesh.service
  test -f /host/etc/re8ch/control-pod-mesh/control_plane_mesh.py
  cp /config/control_pod_mesh.py /host/etc/re8ch/control-pod-mesh/control_pod_mesh.py.next
  chmod 644 /host/etc/re8ch/control-pod-mesh/control_pod_mesh.py.next
  mv /host/etc/re8ch/control-pod-mesh/control_pod_mesh.py.next /host/etc/re8ch/control-pod-mesh/control_pod_mesh.py
else
  while [ ! -f /host/run/re8ch-public-mesh-handoff-v1 ]; do
    echo 'waiting for PodCIDR writer handoff support'
    sleep 10
  done
fi
# Adoption includes exact public-key and FRR-list ownership. No private keys.
cp /config/adoption.json "$root/adoption.json.next"
chmod 600 "$root/adoption.json.next"
mv "$root/adoption.json.next" "$root/adoption.json"
while true; do
  cp /config/package.json "$root/desired.json.next"
  chmod 600 "$root/desired.json.next"
  mv "$root/desired.json.next" "$root/desired.json"
  token="$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)"
  if host /usr/bin/curl --fail --silent --show-error --max-time 8 \
    --cacert /var/lib/re8ch/host-network/kube-ca.crt \
    -H "Authorization: Bearer $token" \
    "https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}/apis/discovery.k8s.io/v1/namespaces/kube-system/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Dadvanced-fabric-dns" \
    > /tmp/host-network-endpoints.json; then
    jq --argjson now "$(date +%s)" '{observedAt:$now,dnsReplicas:([.items[].endpoints[] | select(.conditions.ready == true) | .addresses[]] | unique)}' /tmp/host-network-endpoints.json > "$root/health-targets.json.next"
    chmod 600 "$root/health-targets.json.next"
    mv "$root/health-targets.json.next" "$root/health-targets.json"
  fi
  unset token
  host systemctl enable --now re8ch-host-network.timer
  host systemctl show re8ch-host-network.service --property=Result --property=ActiveState
  sleep 30
done
