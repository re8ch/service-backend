# Ops MCP Server

This directory tracks the source code for the public Ops MCP server currently deployed in the `ops` namespace.

Current live entry:

```text
https://ops.re8ch.com/mcp
```

The server code is stored in Kubernetes as ConfigMap `ops/ops-mcp-server-code` and mounted at `/app/app.py`.

## Files

| File | Purpose |
| --- | --- |
| `ops-mcp-server-app.py` | Source code synced from the live `ops-mcp-server-code` ConfigMap |

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
