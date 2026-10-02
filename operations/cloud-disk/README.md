# Cluster cloud disk

`clusters/re8ch/components/cloud-disk` runs rclone's embedded GUI inside the
cluster. The Pod mounts a dedicated `cephfs-re8ch` PVC at `/workspace`. Its
`cephfs:` remote is an alias for that path; COS, OSS and GCS bucket aliases
come from the SOPS-encrypted Secret. The current inventory contains 37 COS,
10 OSS and 5 GCS buckets.

The GUI and its remote-control API bind inside the Pod, with no public route.
Remote control can read configured cloud credentials and operate on every
remote, so it must not be attached directly to public edge. Access it through
an authenticated Kubernetes Pod port forward while a scoped public proxy is
designed. The workload runs as a single replica with `Recreate` strategy; it
does not mount any other tenant's PVC.

## Refresh credentials and bucket aliases

On the Macmini, refresh the local bucket inventory, then encrypt the Secret:

```sh
~/.local/bin/rclone-cloud-refresh
python operations/cloud-disk/render-secret.py
```

The renderer reads existing tccli and aliyun CLI profiles plus the dedicated
GCS service-account key. It writes only the encrypted Secret into the
worktree. Keep the local credential files and `~/.config/rclone/cluster-gui-auth.json`
outside Git. Re-run the renderer when credentials rotate or bucket aliases
change, then use a signed GitOps commit, CI and a pull request.

## Access and analysis

With a cluster-authorized kubeconfig, forward both GUI ports from the Pod:

```sh
kubectl -n cloud-disk get pods -l app.kubernetes.io/name=cloud-disk
kubectl -n cloud-disk port-forward deployment/cloud-disk 5580:5580 5581:5581
```

Browse `http://127.0.0.1:5580/` and use the GUI credentials from the
cluster's `cloud-disk-credentials` Secret. Never expose port 5581 through an
HTTPRoute or public Service.

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
