# RE8CH storage fabric

## Cluster authority

The storage control plane is the three-voter K3s cluster on restricted-node-6dc3f140f9
(`192.0.2.178`), A1 (`192.0.2.176`), and B1 (`192.0.2.4`). These are
member addresses rather than an HA VIP. Never run this chart through the old
`192.0.2.192:6443` endpoint: that address now belongs to the
`vm-20-12-ubuntu` worker and no longer serves the Kubernetes API. Before any
storage mutation, verify the selected member with `/readyz`, node readiness,
Rook/Ceph health, and the Qwen smoke endpoint.

This chart inventories visible Alibaba OSS and Tencent COS buckets and can
install the rclone CSI driver. It keeps object stores parallel to CephFS:
CephFS provides shared POSIX storage, while object mounts provide assets,
models, backups, archives, and node-cached datasets.

The chart never contains cloud credentials. Create provider-specific Secrets
out of band with least privilege and reference them through `providers`.
Deleting a generated PVC or PV must not delete bucket data; all generated PVs
use `Retain`.

Multiple PVCs may reference one bucket when they use distinct prefixes. Treat
multiple writable mounts of the same prefix as unsupported because independent
rclone VFS caches do not provide coherent distributed locking.

## Managed local datasets and fallback

When `managedDatasets.enabled=true`, the chart creates one CephFS PVC and one
additive synchronization CronJob per restricted-node-6dc3f140f9 dataset. R640-backed Ceph pools are
intentionally single-copy: Kubernetes and the Ceph control plane remain HA,
but these datasets follow the physical restricted-node-6dc3f140f9 disk and are unavailable while the
host is down.

The current data placement is:

- `/srv/nas/shared` is a CephFS primary; a systemd timer mirrors it to the
  private ext4 bind mount `/srv/nas-local-fallback`.
- Pigsty live PostgreSQL data uses the host-mapped `r640-rbd/pigsty-r640` image;
  pgBackRest repositories and logical dumps are mirrored into managed CephFS.
- Neo4j live data PVCs use `rbd-r640`; the released local-path PVs are retained
  during the rollback window, and logical Cypher exports are mirrored to CephFS.
- model assets are mirrored into the managed restricted-node-6dc3f140f9 CephFS data pool.

Fallback is deliberately one-way and single-writer. It is a recovery copy, not
an automatic dual-writer failover. Install the units in `systemd/` on restricted-node-6dc3f140f9;
`re8ch-nas-fallback-bind.service` makes the local view private before the CephFS
primary mount, preventing mount propagation from hiding the fallback copy.

The upstream rclone CSI chart is vendored at version 0.5.0. Object PVs default
to rclone's `mount2` backend so the privileged node plugin can use a direct
mount path and avoid Ubuntu 26's host-side `fusermount3` AppArmor denial. The
node-side `rclone` container also has a scoped unconfined setting; the host
AppArmor profile remains enabled.

Ubuntu 26 additionally confines the setuid `fusermount3` helper independently
of the container. On restricted-node-6dc3f140f9, install `host-apparmor/r640-fusermount3` as
`/etc/apparmor.d/local/fusermount3` and reload `/etc/apparmor.d/fusermount3`.
The rule admits only `fuse.rclone` mounts below rclone CSI PV paths; it does not
disable the distro profile.

Render without installing the CSI dependency:

```sh
helm template storage-fabric . --namespace storage-fabric
```

Enable the dependency only after its images are available on eligible nodes:

```sh
helm dependency build
helm upgrade --install storage-fabric . --namespace storage-fabric \
  --create-namespace --set rcloneCsi.enabled=true
```

`values-probe.yaml` creates two isolated prefixes in the selected OSS bucket
and two in the selected COS bucket. They intentionally share credentials but
not writable paths, which exercises the supported same-bucket/multiple-PVC
case without relying on cross-mount VFS cache coherence.
