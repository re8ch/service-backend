# Ops MCP Server

This directory tracks the source code for the public Ops MCP server currently deployed in the `ops` namespace.

Legacy live entry (clients should migrate to the ToolHive-published surfaces):

```text
https://ops.re8ch.com/mcp
```

ToolHive is the final publication and policy layer. Its manifests are in
`../toolhive/`. The raw endpoint remains an upstream compatibility endpoint and
must not be added directly to new Agents.

The active K3s control planes are currently `192.0.2.176` (A1) and
`192.0.2.4` (B1). The former `192.0.2.178` VIP and the
`k3s-api.internal.re8ch.com -> 192.0.2.192` record are stale as of 2026-08-04;
`KUBE_SERVER_PUBLIC` temporarily uses A1 until the HA VIP/DNS is repaired.

The server code is stored in Kubernetes as ConfigMap `ops/ops-mcp-server-code` and mounted at `/app/app.py`.

## Files

| File | Purpose |
| --- | --- |
| `ops-mcp-server-app.py` | Source code synced from the live `ops-mcp-server-code` ConfigMap |
| `ops-mcp-server-standardization.patch.yaml` | Resource/security baseline and current control-plane endpoint |
| `coredns-availability.patch.yaml` | Bootstrap patch for an already-created CoreDNS Deployment |
| `coredns-availability-policy.yaml` | Durable Kubernetes v1 admission policy that preserves two replicas and reviewed placement when the K3s Addon rewrites CoreDNS |

The durable policy places CoreDNS only on nodes known to cache the pinned system
image: `harbor-registry`, `qianwen-nuclio-79`, and `vm-20-12-ubuntu`. It adds the
specific `anchor=true:NoSchedule` toleration required by the VM node and rewrites
that toleration idempotently on every Deployment update. During the 2026-08-04
qianwen node outage, the second replica moved to the VM node while the first
remained on Harbor; both became Ready without an external image pull.

## Apply Code To Cluster

Run from an operator machine that can SSH to ICP:

```bash
python3 -m py_compile /Volumes/A1/cloud-functions/manifests/k3s/ops/ops-mcp-server-app.py

scp /Volumes/A1/cloud-functions/manifests/k3s/ops/ops-mcp-server-app.py ICP:/tmp/ops-mcp-app.py

ssh ICP '
  sudo k3s kubectl -n ops get configmap ops-mcp-server-code -o yaml > /tmp/ops-mcp-server-code.bak.$(date +%Y%m%d%H%M%S).yaml
  sudo k3s kubectl -n ops create configmap ops-mcp-server-code \
    --from-file=app.py=/tmp/ops-mcp-app.py \
    --dry-run=client -o yaml | sudo k3s kubectl apply -f -
  sudo k3s kubectl -n ops patch deployment ops-mcp-server \
    --type=strategic --patch-file /tmp/ops-mcp-server-standardization.patch.yaml
  sudo k3s kubectl -n ops rollout restart deploy/ops-mcp-server
  sudo k3s kubectl -n ops rollout status deploy/ops-mcp-server --timeout=180s
'
```

## Verify

```bash
ssh ICP '
  TOKEN=$(sudo k3s kubectl -n ops get secret ops-mcp-auth -o jsonpath="{.data.api-token}" | base64 -d)
  curl -skS -H "Authorization: Bearer $TOKEN" https://ops.re8ch.com/api/grafana/access
'
```

The response includes a tenant-scoped Grafana service account token and instructions for AI agents. Do not paste that token into documentation.
