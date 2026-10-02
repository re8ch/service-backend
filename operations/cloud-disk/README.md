# Cluster cloud disk

`clusters/re8ch/components/cloud-disk` runs rclone's embedded GUI inside the
cluster. The Pod mounts a dedicated `cephfs-re8ch` PVC at `/workspace`. Its
`cephfs:` remote is an alias for that path; COS, OSS and GCS bucket aliases
come from the SOPS-encrypted Secret. The current inventory contains 37 COS,
10 OSS and 5 GCS buckets.

The public file manager is `https://cloud-disk.service.re8ch.com`. Its OAuth
session is issued by RE8CH Auth Gateway and restricted to the current
`re8ch:k8s-admins` group. A dedicated gateway runs allowlisted rclone CLI
operations; the original GUI and remote-control API remain private to the Pod.
The workload runs as a single replica with `Recreate` strategy and does not
mount any other tenant's PVC.

Admin vMCP at `https://admin.re8ch.com/mcp` aggregates the `cloud-disk-mcp`
tools. Small file content is base64 encoded (up to 1 MiB); larger transfers use
single-use 10-minute links scoped to one path and HTTP method. Cross-cloud
copy, move, and sync run asynchronously and return a job ID. Sync requires an
explicit `deleteDestination=true`. The cloud-disk gateway records sanitized
operation events with remote, path, source, result, and job ID. It never
returns rclone configuration or cloud credentials.
Each content operation returns a `traceId`; pass the same ID to Runtime
Anatomy's explicit analysis to correlate gateway audit with CephFS events.
Provider-native object audit remains necessary to independently verify COS,
OSS, or GCS object-key access outside this gateway.

## Refresh credentials and bucket aliases

On the Macmini, refresh the local bucket inventory, then encrypt the Secret:

```sh
python operations/cloud-disk/refresh-buckets.py
python operations/cloud-disk/render-secret.py
```

The renderer reads existing tccli and aliyun CLI profiles plus the dedicated
GCS service-account key. It writes only the encrypted Secret into the
worktree. Keep the local credential files and `~/.config/rclone/cluster-gui-auth.json`
outside Git. Re-run the renderer when credentials rotate or bucket aliases
change, then use a signed GitOps commit, CI and a pull request.

The Auth Gateway and cloud-disk gateway images are pinned by digest and signed
with the public key in `operations/cloud-disk/image-signing.pub`. Verify a
replacement digest with `cosign verify --key operations/cloud-disk/image-signing.pub IMAGE@sha256:DIGEST`
before promotion. The signing private key is local to the Macmini and is not
stored in this repository.

## Access and analysis

With a cluster-authorized kubeconfig, forward both GUI ports from the Pod:

```sh
kubectl -n cloud-disk get pods -l app.kubernetes.io/name=cloud-disk
kubectl -n cloud-disk port-forward deployment/cloud-disk 5580:5580 5581:5581
```

Browse `http://127.0.0.1:5580/` and use the GUI credentials from the
cluster's `cloud-disk-credentials` Secret for private maintenance. Never expose
port 5581 through an HTTPRoute or public Service.

Runtime Anatomy's `RuntimeAnalysis` can observe the rclone container's
CephFS access through the `/workspace` PVC mount. Start a bounded analysis
while the GUI performs a CephFS operation:

```sh
python operations/runtime-anatomy/cli.py -n cloud-disk analyze start \
  --pod <current-cloud-disk-pod> --container rclone \
  --volume /workspace=cloud-disk-workspace --wait
```

Remote COS, OSS and GCS object-key operations require provider audit evidence;
TLS traffic alone cannot establish which keys were accessed. Runtime Anatomy
reports missing object audit evidence as incomplete. Its commit and restore
controller remains confined to labelled canary sources, so this production
workspace is analysis-only until a reviewed production capture policy exists.
