# Ops MCP Server

This directory tracks the source code for the public Ops MCP server currently deployed in the `ops` namespace.

Legacy live entry (clients should migrate to the ToolHive-published surfaces):

```text
https://ops.re8ch.com/mcp
```

ToolHive is the final publication and policy layer. Its manifests are in
`../toolhive/`. The raw endpoint remains an upstream compatibility endpoint and
must not be added directly to new Agents.

The embedded-etcd control plane has three voters: `192.0.2.183` (restricted-node-6dc3f140f9),
`192.0.2.18` (A1), and `192.0.2.208` (B1). These are member addresses,
not a shared API VIP. As of 2026-08-15, A1 and B1 pass the Kubernetes
`/readyz` check from the operator network; validate restricted-node-6dc3f140f9 routing independently
before selecting it as a client endpoint. `KUBE_SERVER_PUBLIC` uses A1.

`192.0.2.67` belongs to the `vm-20-12-ubuntu` worker and is a retired K3s API
endpoint. The historical `k3s-api.internal.re8ch.com -> 192.0.2.67` record and
locally generated `icp-k3s-direct` kubeconfigs must not be used for cluster
management. A kubeconfig error returning HTTP 404 during API discovery usually
means the client reached a non-apiserver listener on this old node, not that the
current three-member control plane is empty.

The server code is stored in Kubernetes as ConfigMap `ops/ops-mcp-server-code` and mounted at `/app/app.py`.

## Files

| File | Purpose |
| --- | --- |
| `ops-mcp-server-app.py` | Source code synced from the live `ops-mcp-server-code` ConfigMap |
| `ops-mcp-server-standardization.patch.yaml` | Resource/security baseline and current control-plane endpoint |
Cluster DNS is owned by the Advanced Fabric HelmRelease. Ops no longer patches
or admits a K3s-managed DNS Deployment.

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
