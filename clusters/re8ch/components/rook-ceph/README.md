# RE8CH Ceph storage

This directory defines a single-capacity-node Rook/Ceph cluster. B1 is the
sole Ceph coordinator, R640 is the sole OSD host, and A1/B1 expose only
node-local storage. All Ceph pools are single-copy R640 working sets and must
be recoverable from an external backup.

## Safety boundary

- Rook and Ceph are pinned to v1.20.3 and v20.2.2 respectively.
- Only explicitly listed devices are eligible. Never enable `useAllDevices`.
- Only R640 contributes an OSD. Its dedicated LV is the sole Ceph capacity
  device; A1/B1 are explicitly ineligible for OSD discovery.
- `cephfs-r640` is non-default, uses `Retain`, and is the only class for new
  CephFS claims. `cephfs-pilot` and `cephfs-re8ch` are migration-only and
  admission rejects new claims.
- `storage.re8ch.com/ceph-pilot=true` identifies R640 as the only Ceph capacity
  domain. `storage.re8ch.com/ceph-client=true` independently enables the CSI
  node client; a client label never authorizes disk discovery or OSD creation.
- CephFS metadata and data pools use the R640 device class with `size: 1`.
  Metadata redundancy is not claimed; loss of R640 makes the filesystem
  unavailable even if MON/MGR/MDS on B1 remain running.
- The legacy replicated data pool remains only while existing PVCs migrate. It
  must not be removed by the topology change itself.
- After their OSDs are safely removed, A1/B1 reuse their dedicated VHDX devices
  as XFS node-local pools. `local-a1` and `local-b1` are static-PV classes with
  `Retain`; every PV must pin itself to exactly one matching hostname.
- R640 uses a dedicated LVM logical volume from currently free extents. Existing
  LVs and filesystems are not resized.

## R640 edge topology

R640 is not a ZeroTier endpoint. The MikroTik at `192.168.88.1` owns the
`10.181.22.128/24` ZeroTier interface and the public/NAT edge. R640 itself has
`192.168.88.251` on the LAN plus a dummy `10.181.22.128/32` used as its K3s
node identity. Do not add a `10.181.22.0/24` route to R640 Linux; its correct
return path is the MikroTik default gateway.

Ceph host-network daemons bind the advertised `/32` identity. The edge therefore
needs both of the following narrowly scoped translations:

- MikroTik DNATs TCP `3300`, `6789`, `6800-7568`, and `9283` received for
  `10.181.22.128` on `zt-re8ch-control` to `192.168.88.251`.
- `re8ch-r640-ceph-edge.service` loads `r640-ceph-edge.nft` on R640 and restores
  the destination from the LAN address to the local `/32` identity. The rule
  matches only `eno2np1`, the R640 LAN address, and the Ceph port set.

Before changing RouterOS, save a RouterOS export and system backup. Add three
Ceph-only rules named `re8ch r640 ceph zt proxy dnat`, `re8ch r640 ceph zt
proxy srcnat`, and `re8ch r640 ceph zt proxy forward`; keep the existing K3s
rules unchanged. Do not create a broad all-port forward and do not change
public DNS. The initial installation backup is
`pre-ceph-edge-20260808-0040` (both RouterOS export and system backup). Install
the R640 files with:

```sh
sudo install -d -m 0755 /etc/rook-ceph
sudo install -m 0644 r640-ceph-edge.nft /etc/rook-ceph/r640-ceph-edge.nft
sudo install -m 0644 re8ch-r640-ceph-edge.service \
  /etc/systemd/system/re8ch-r640-ceph-edge.service
sudo systemctl daemon-reload
sudo systemctl enable --now re8ch-r640-ceph-edge.service
```

Stopping the service removes only the dedicated `ip re8ch_ceph_edge` nftables
table. Removing the three rules by their exact comments is the RouterOS
rollback.

## Device preparation

On R640, create `/dev/ubuntu-vg/ceph_pilot_osd` with 20 GiB from free VG space.
On A1 and B1, run `prepare-wsl-ceph-vhd.ps1` as Windows Administrator to create
`D:\RE8CH-Ceph\ceph-pilot-osd.vhdx` and attach it with
`wsl --mount --vhd --bare`. Install/enable `rook-ceph-wsl-vhd.service` so the
disk is attached before K3s after a WSL or Windows restart. Record the resulting
Linux device and serial before updating `cluster-values.yaml`.

The device identities retained for retirement and local-pool conversion are:

- A1: `/dev/disk/by-id/scsi-360022480b66b60a8f0c873bb147edef8`
- B1: `/dev/disk/by-id/scsi-36002248092fe886403f06a91bb13700e`
- R640: `/dev/ubuntu-vg/ceph_pilot_osd`

Never format the A1/B1 devices until their OSDs are out, safe-to-destroy, and
removed from the CRUSH map. Formatting is destructive and requires a verified
external backup plus an exported Ceph inventory.

The obsolete loop-image service is intentionally not part of this pilot. On A1
and B1 its former unit is disabled, but the old host-side image is retained for
manual review rather than deleted during rollout. `allowLoopDevices` remains
false.

## WSL image bootstrap

The CSI node registrar normally comes from
`registry.k8s.io/sig-storage/csi-node-driver-registrar:v2.17.0`. If that
registry is unreachable from A1/B1, the tested bootstrap is to pull the same
linux/amd64 manifest from
`registry.aliyuncs.com/google_containers/csi-node-driver-registrar:v2.17.0`,
verify its platform manifest digest is
`sha256:0fc05c749072bea889beffc97499f6836f74aebe351c78ff8d90d671c35f04da`,
and tag it locally with the official registry name. Do not change the Helm
values to use an unverified mirror globally; the multi-architecture index
digest is expected to differ from the linux/amd64 manifest digest.

The production Ceph cluster requires Messenger v2 secure mode. Therefore
`csi-drivers-values.yaml` sets the kernel CephFS option `ms_mode=secure`.
Without an explicit v2 mode, the kernel client can send a v1 banner to port
3300, producing a misleading `no mds` mount error even when MDS is healthy.

## Reconciliation

Flux owns the `rook-ceph`, `ceph-csi-drivers`, and `rook-ceph-cluster` Helm
releases through `releases.yaml`. Their values are generated into immutable
reviewed ConfigMaps by the root Kustomization. Do not run `helm upgrade`
directly against these releases.

Do not install the cluster chart until the operator deployment and discovery
pods are healthy.

MON, MGR, MDS, the Rook operator and CSI controllers require
`storage.re8ch.com/coordinator=true`, which is assigned only to B1. The B1
coordinator taint excludes ordinary workloads; Ceph components explicitly
tolerate it. CSI node plugins remain available on every labeled client node.

`cluster-values-production-encrypted.yaml` is the applied production encryption
overlay. It was rolled out only after all MONs, the manager and PGs were
healthy. It enables native Ceph
Messenger v2 encryption. The optional Mihomo component of
`re8ch-network-fabric` is used only for public registry bootstrap and must
remain excluded from Ceph daemon and CSI traffic. See
`docs/cephfs-network-fabric-contract-2026-08-13.md` for the application
boundary and promotion gates.

## Acceptance

1. Exactly one MON, one MGR and one active MDS run on B1; only R640 OSD 0 is
   `up` and `in`.
2. All PGs are `active+clean` before and after each rollout phase.
3. `cephfs-r640` exists, is non-default and advertises single-node/backup-required;
   new `cephfs-pilot` and `cephfs-re8ch` claims are denied.
4. A disposable `cephfs-r640` claim mounts and passes cross-node read/write.
5. Existing tenant pods and public smoke endpoints remain healthy.
6. Desired and observed CephFilesystem generations match and no standby MDS
   remains.
7. CephFS subvolumegroup and subvolume listing complete without timeout.
8. `local-a1` and `local-b1` have no dynamic provisioner. Every static PV uses
   `Retain`, declares the correct node affinity, and is consumed only by a
   workload intentionally bound to that node.

The smoke namespace is disposable, but deleting its retained PV does not erase
the CephFS subvolume automatically. Perform that cleanup only after explicitly
confirming it contains no needed data.

## Operations references

- [Headlamp CephFS plugin PVC runbook](../../../docs/headlamp-cephfs-plugin-pvc-runbook-2026-08-21.md)
- [Rook CephFS MDS placement and CSI dependency policy](../../../docs/rook-cephfs-mds-placement-policy-2026-08-21.md)
- [Headlamp plugin CephFS CSI incident](../../../docs/incidents/2026-08-21-headlamp-cephfs-csi-control-plane.md)
- [CephFS network-fabric contract](../../../docs/cephfs-network-fabric-contract-2026-08-13.md)
