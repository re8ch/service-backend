# RE8CH Plugin Runtime

This is the generic adapter between reviewed infrastructure declarations and
ToolHive. It derives MCP tools at runtime; domain-specific Python MCP servers
are not required.

## Build and validate

```sh
python3 -m unittest -v test_runtime.py
kubectl kustomize . >/tmp/re8ch-plugin-runtime.yaml
docker build -t registry.re8ch.com/functions-shared/re8ch-plugin-runtime:0.1.2 .
```

Build outside the cluster, scan/sign the image, push it to Harbor, then pin the
digest in `runtime.yaml`. Do not deploy the mutable tag shown in the source
manifest to production.

## Reconciliation

1. Git owns `plugins/*.json`, `registry.json`, and policy changes.
2. The runtime atomically reloads valid definitions every 30 seconds. Invalid
   generations leave the previous generation active.
3. `plugins.enable` and `plugins.disable` are emergency in-memory overrides and
   return `drift: true`; reconcile them into Git.
4. ToolHive Registry indexes the Git and Kubernetes sources. The vMCP remains
   the only client-facing MCP endpoint.
5. `vmcp-aggregation-patch.yaml` is not part of the Kustomize bundle. Read the
   live aggregation first, verify its two existing workloads match the patch,
   then apply it with an explicit merge patch so SSA does not steal ownership.

The legacy Cloud Platform and Cluster Infra deployments remain untouched as
rollback paths until the generated tools pass the acceptance matrix.
