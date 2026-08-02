# OpenSandbox on restricted-node-6dc3f140f9

This deployment bootstraps the OpenSandbox Kubernetes controller and lifecycle
server for Teable integration testing. It deliberately does not enable Teable's
Agent Runtime until a matching official Teable Sandbox Agent image is available.

## Versions and placement

- Controller Helm chart: `0.2.0`
- Lifecycle server: `opensandbox/server:v0.2.2`, pinned by digest in the manifest
- `execd`: `opensandbox/execd:v1.0.21`
- Initial node: `restricted-node-6dc3f140f9`
- Lifecycle API: `opensandbox-server.opensandbox-system.svc:80`
- Teable compatibility API: `teable-infra.opensandbox-system.svc:8080`

The controller and server tolerate restricted-node-6dc3f140f9's `dedicated=pg-re8ch:NoSchedule` taint.
Sandbox pods use a negative, non-preempting PriorityClass and hard resource
limits. Move them to a dedicated worker by changing the
`opensandbox.re8ch.com/workload` node label and the template node selector.

Server metadata uses a statically bound, `Retain` restricted-node-6dc3f140f9 PV at
`/var/lib/re8ch/opensandbox-server`. This avoids depending on the cluster's
currently unhealthy dynamic `local-path-provisioner`.

## Installation

Create the API keys directly in Kubernetes; never commit them:

```bash
openssl rand -hex 32 > /tmp/opensandbox-api-key
openssl rand -hex 32 > /tmp/teable-infra-api-key
kubectl create namespace opensandbox-system --dry-run=client -o yaml | kubectl apply -f -
kubectl -n opensandbox-system create secret generic opensandbox-api-key \
  --from-file=api-key=/tmp/opensandbox-api-key \
  --from-file=teable-api-key=/tmp/teable-infra-api-key
```

Apply `platform.yaml`, then install the controller:

```bash
kubectl apply -f platform.yaml
helm upgrade --install opensandbox-controller \
  https://github.com/opensandbox-group/OpenSandbox/releases/download/helm/opensandbox-controller/0.2.0/opensandbox-controller-0.2.0.tgz \
  --namespace opensandbox-system --create-namespace \
  --values controller-values.yaml
```

## Teable activation gate

The current Teable image requires a sandbox image containing
`/usr/local/bin/teable-sandbox-agent-entrypoint`. No verifiable public Teable
image was found as of 2026-08-03. Do not set `SANDBOX_PROVIDER` before obtaining
that image from Teable Enterprise, mirroring it into Harbor, scanning/signing it,
and pinning it by digest.

The compatibility service advertises `opensandbox.v1`, `image-preheat.v1`, and
`sandbox-metadata.v1`. Image preheat creates a constrained pull Pod on restricted-node-6dc3f140f9 and
reports readiness from Kubernetes `imageID`; metadata updates are persisted as
an annotation on the corresponding BatchSandbox. It does not pretend to
implement app runtime, Git hosting, or S3. Those require the complete Teable
Infra distribution.

Stage 1 connects Teable using `TEABLE_INFRA_API_URL` and an
`agent-collab/teable-infra-runtime` Secret. `SANDBOX_PROVIDER` remains unset, so
the connection can be diagnosed without allowing Teable to launch an image that
lacks its required Agent entrypoint.
