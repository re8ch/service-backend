#!/usr/bin/env bash
set -euo pipefail

# Create one isolated regional peer without reconciling existing peers.
: "${REGION_ID:?lower-case region id, for example gz-1}"
: "${NODE_NAME:?existing Ready K3s node name}"
: "${NETBIRD_SETUP_KEY:?NetBird setup key}"
: "${ROUTER_GROUP:?auto-assigned NetBird routing-peer group}"
: "${KUBECTL:=sudo k3s kubectl}"
: "${NETBIRD_NAMESPACE:=netbird-access}"
: "${NETBIRD_IMAGE:=netbirdio/netbird:0.74.6}"
: "${WIREGUARD_PORT:=51820}"

case "$REGION_ID" in
  *[!a-z0-9-]*|'') echo "REGION_ID must match [a-z0-9-]+" >&2; exit 2 ;;
esac
case "$WIREGUARD_PORT" in
  *[!0-9]*|'') echo "WIREGUARD_PORT must be numeric" >&2; exit 2 ;;
esac

deployment="netbird-router-${REGION_ID}"
secret="netbird-routing-peer-${REGION_ID}"
hostname="re8ch-router-${REGION_ID}"
ready="$(${KUBECTL} get node "$NODE_NAME" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')"
[[ "$ready" == True ]] || { echo "Node $NODE_NAME is not Ready" >&2; exit 1; }
if ${KUBECTL} -n "$NETBIRD_NAMESPACE" get deployment "$deployment" >/dev/null 2>&1; then
  echo "Refusing to mutate existing deployment $deployment" >&2
  exit 1
fi

${KUBECTL} -n "$NETBIRD_NAMESPACE" create secret generic "$secret" \
  --from-literal=NB_SETUP_KEY="$NETBIRD_SETUP_KEY" \
  --dry-run=client -o yaml | ${KUBECTL} apply -f - >/dev/null

${KUBECTL} apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${deployment}
  namespace: ${NETBIRD_NAMESPACE}
  labels:
    app.kubernetes.io/name: netbird-router
    app.kubernetes.io/instance: ${REGION_ID}
    re8ch.com/netbird-router-group: ${ROUTER_GROUP}
spec:
  replicas: 1
  strategy: {type: Recreate}
  selector:
    matchLabels: {app.kubernetes.io/name: netbird-router, app.kubernetes.io/instance: ${REGION_ID}}
  template:
    metadata:
      labels: {app.kubernetes.io/name: netbird-router, app.kubernetes.io/instance: ${REGION_ID}}
    spec:
      nodeSelector: {kubernetes.io/hostname: ${NODE_NAME}}
      automountServiceAccountToken: false
      containers:
        - name: netbird
          image: ${NETBIRD_IMAGE}
          imagePullPolicy: IfNotPresent
          ports:
            - {name: wireguard, containerPort: ${WIREGUARD_PORT}, hostPort: ${WIREGUARD_PORT}, protocol: UDP}
          env:
            - name: NB_SETUP_KEY
              valueFrom: {secretKeyRef: {name: ${secret}, key: NB_SETUP_KEY}}
            - {name: NB_MANAGEMENT_URL, value: "https://access.re8ch.com:8443"}
            - {name: NB_HOSTNAME, value: ${hostname}}
          securityContext:
            allowPrivilegeEscalation: true
            capabilities: {add: [NET_ADMIN, SYS_ADMIN, SYS_RESOURCE]}
          resources:
            requests: {cpu: 100m, memory: 128Mi}
            limits: {cpu: "1", memory: 768Mi}
          startupProbe:
            exec: {command: [netbird, status, --check, startup]}
            periodSeconds: 3
            timeoutSeconds: 10
            failureThreshold: 40
          readinessProbe:
            exec: {command: [netbird, status, --check, ready]}
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          livenessProbe:
            exec: {command: [netbird, status, --check, live]}
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 3
          volumeMounts:
            - {name: tun, mountPath: /dev/net/tun}
            - {name: state, mountPath: /var/lib/netbird}
      volumes:
        - name: tun
          hostPath: {path: /dev/net/tun, type: CharDevice}
        - name: state
          hostPath: {path: /var/lib/rancher/netbird/${REGION_ID}, type: DirectoryOrCreate}
EOF

${KUBECTL} -n "$NETBIRD_NAMESPACE" rollout status "deployment/$deployment" --timeout=5m
${KUBECTL} -n "$NETBIRD_NAMESPACE" exec "deployment/$deployment" -- netbird status --check ready
echo "Peer $hostname is ready; keep NB_HOSTNAME stable and set only its Dashboard display name."
