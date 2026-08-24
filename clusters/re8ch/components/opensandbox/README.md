# OpenSandbox on R640

This deployment bootstraps the OpenSandbox Kubernetes controller and lifecycle
server and the storage/source services required by Teable Agent Runtime.

## Versions and placement

- Controller Helm chart: `0.2.0`
- Lifecycle server: `opensandbox/server:v0.2.2`, pinned by digest in the manifest
- `execd`: `opensandbox/execd:v1.0.21`
- Initial node: `r640`
- Lifecycle API: `opensandbox-server.opensandbox-system.svc:80`
- Teable compatibility API: `teable-infra.opensandbox-system.svc:8080`

The controller and server tolerate R640's `dedicated=pg-re8ch:NoSchedule` taint.
Sandbox pods use a negative, non-preempting PriorityClass and hard resource
limits. Move them to a dedicated worker by changing the
`opensandbox.re8ch.com/workload` node label and the template node selector.

Server metadata uses a statically bound, `Retain` R640 PV at
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

## Teable Agent Runtime

The official image is pinned as
`teableio/teable-sandbox-agent@sha256:724b1c8f341e144bc5502c10262baeb686f59d381949a69900930500306479f9`.
Its `/usr/local/bin/teable-sandbox-agent-entrypoint` and OpenSandbox lifecycle
were validated on R640 before enabling `SANDBOX_PROVIDER=opensandbox`.

The compatibility service also advertises `git-registry.smart-http.v1` and
`s3-compat.v1`. Image preheat creates a constrained pull Pod on R640 and
reports readiness from Kubernetes `imageID`; metadata updates are persisted as
an annotation on the corresponding BatchSandbox. `teable-infra-storage.yaml`
deploys MinIO and a Bearer-authenticated Git Smart HTTP service on independent
R640 Retain PVs. Only `app-runtime.gateway.v1` remains intentionally absent.

Apply `teable-infra-storage.yaml` after creating the uncommitted
`teable-infra-storage` Secret. MinIO and Git stay ClusterIP-only and are reached
through the authenticated `teable-infra` facade.

## Free-tier stopping point

The current self-hosted Teable instance reports product level `free`. Its
effective feature limits disable AI Chat, AI fields, and generated apps:

```text
chatAIEnable=false
fieldAIEnable=false
appEnable=false
```

Consequently, the UI error `Need to upgrade the subscription to use this
feature` is a Teable license decision, not an OpenSandbox failure. Backend logs
show the request is rejected by `UsageGuard` before the Sandbox provider runs.
The free-tier deployment is complete at the tested infrastructure boundary:
OpenSandbox lifecycle, official Agent image, image preheat, metadata, MinIO/S3,
and Git Smart HTTP.

`app-runtime.gateway.v1` remains absent and InfraDoctor reports it accurately.
That capability is for generated-app artifact upload and deployment, not AI Chat.
Do not add the capability string until its create/upload/activate/status/URL/
destroy API has a real implementation. Do not bypass Teable's license checks;
configure a legitimate Business/Enterprise license before testing licensed UI
features. See `docs/teable-free-tier-runtime-boundary-2026-08-03.md`.
