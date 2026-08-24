# CubeSandbox browser terminal overlay

This overlay targets CubeSandbox commit
`8721dd151971ce3c2966482bbd32904ad98f378e` (v0.6.0). It adds an xterm-based
PTY page at `/sandboxes/:sandboxID/terminal` and an **Open terminal** action to
the ordinary Sandbox detail page.

Build from a clean checkout of the pinned CubeSandbox commit:

```sh
git apply /path/to/web-terminal.patch
cd web
npm ci
npm run build
docker build -f /path/to/Dockerfile -t registry.re8ch.com/ai-operations/cube-webui-terminal:<version> .
```

The checked-in `cube-webui-terminal-configmaps.yaml` is the currently deployed
static artifact. It keeps the digest-pinned upstream runtime image, expands the
compressed bundle with an init container, and mounts the result over the WebUI
document root. The Deployment overlay is applied as a strategic merge patch:

```sh
kubectl create -f cube-webui-terminal-configmaps.yaml # artifact, first install
kubectl apply -f cube-webui-terminal-configmaps.yaml  # nginx streaming config
kubectl -n cube-trial patch deployment cube-trial-webui \
  --type=strategic --patch-file=cube-webui-terminal-deployment.patch.yaml
```

Do not use `kubectl apply` for the large binary artifact ConfigMap itself:
kubectl's last-applied annotation exceeds Kubernetes' annotation limit. For a
new bundle, patch its `binaryData.dist.tar.gz` value or recreate only that
artifact ConfigMap during a controlled rollout.

Runtime PTY calls stay same-origin under
`/sandbox/<sandbox-id>/49983/process.Process/*` and use the short-lived tokens
returned by Cube's authenticated `connect` endpoint. The WebUI nginx overlay
authenticates that endpoint through CubeOps, then forwards it to the compatible
CubeAPI implementation because CubeOps v0.6.0 still calls CubeMaster's removed
legacy connect path.

## Upgrade procedure

1. Build the pinned source patch with `npm ci && npm run build`.
2. Archive `web/dist`, calculate its SHA-256, and replace only
   `binaryData.dist.tar.gz` in `cube-webui-terminal-configmaps.yaml`.
3. Put the same digest in the Deployment annotation
   `cube.re8ch.com/terminal-dist-sha256`.
4. Patch the artifact ConfigMap with `kubectl patch`; do not use
   `kubectl apply` because the generated last-applied annotation is too large.
5. Apply the nginx ConfigMap and strategic Deployment patch, then wait for the
   WebUI rollout.

The WebUI container mounts `cube-admin-token` read-only. Lua reads it only for
the authenticated connect subrequest and replaces the browser JWT before the
request reaches CubeAPI. Never put this token in the frontend bundle, a command
argument, logs, or this repository.

## Acceptance test

Use a disposable Sandbox and verify the exact browser data path:

1. Authenticate through Dex and Cube login.
2. Create a Sandbox from `tpl-0a3c5cfb6f2847b881b24668`.
3. Open its detail page and select **Open terminal**.
4. Confirm the terminal reaches `connected`, run
   `printf 'WEB_TERMINAL_OK\n'`, resize the browser, and verify further input.
5. Select **End session**, then delete the disposable Sandbox.

A `404 page not found` mentioning CubeMaster means the request fell back to
CubeOps' legacy connect implementation. Check that the dedicated nginx
`/sandboxes/<id>/connect` location is present before investigating envd.

## Rollback

Restore the previous artifact ConfigMap value and digest annotation, remove the
`cube-api-admin-token` volume/mount, restore the previous nginx ConfigMap, and
roll out `cube-trial-webui`. Rollback affects only the browser terminal overlay;
it must not delete Sandboxes, templates, Cube stateful claims, or shared CRDs.
