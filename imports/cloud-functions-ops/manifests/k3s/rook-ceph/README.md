# RE8CH CephFS pilot

This directory introduces a deliberately small Rook/CephFS cluster on `restricted-node-6dc3f140f9`,
`restricted-node-1eb0570f74`, and `restricted-node-296f36f2fb`. It is for cluster coordination files and
cross-node shared state. It does not replace Pigsty, Harbor storage, etcd, or
existing `local-path` volumes.

## Safety boundary

- Rook and Ceph are pinned to v1.20.3 and v20.2.2 respectively.
- Only explicitly listed devices are eligible. Never enable `useAllDevices`.
- A1 and B1 each contribute one explicitly capped 200 GiB pilot OSD. restricted-node-6dc3f140f9
  initially contributes a 20 GiB LV; it can be extended later because its
  storage inventory is locally verified.
- `cephfs-pilot` is not the default StorageClass and uses `Retain`.
- Pools use `size: 3`, `min_size: 2` (Ceph safe default), with `host` failure
  domains.
- A1/B1 use dedicated, dynamic 200 GiB VHDX files on their Windows `D:` volumes,
  attached to WSL as raw disks. The VHDX files must never be placed on restricted-node-6dc3f140f9 NAS.
- restricted-node-6dc3f140f9 uses a dedicated LVM logical volume from currently free extents. Existing
  LVs and filesystems are not resized.

## restricted-node-6dc3f140f9 edge topology

restricted-node-6dc3f140f9 is not a ZeroTier endpoint. The MikroTik at `192.0.2.131` owns the
`192.0.2.183/24` ZeroTier interface and the public/NAT edge. restricted-node-6dc3f140f9 itself has
`192.0.2.97` on the LAN plus a dummy `192.0.2.183/32` used as its K3s
node identity. Do not add a `192.0.2.253/24` route to restricted-node-6dc3f140f9 Linux; its correct
return path is the MikroTik default gateway.

Ceph host-network daemons bind the advertised `/32` identity. The edge therefore
needs both of the following narrowly scoped translations:

- MikroTik DNATs TCP `3300`, `6789`, `6800-7568`, and `9283` received for
  `192.0.2.183` on `zt-re8ch-control` to `192.0.2.97`.
- `re8ch-r640-ceph-edge.service` loads `r640-ceph-edge.nft` on restricted-node-6dc3f140f9 and restores
  the destination from the LAN address to the local `/32` identity. The rule
  matches only `eno2np1`, the restricted-node-6dc3f140f9 LAN address, and the Ceph port set.

Before changing RouterOS, save a RouterOS export and system backup. Add three
Ceph-only rules named `re8ch restricted-node-6dc3f140f9 ceph zt proxy dnat`, `re8ch restricted-node-6dc3f140f9 ceph zt
proxy srcnat`, and `re8ch restricted-node-6dc3f140f9 ceph zt proxy forward`; keep the existing K3s
rules unchanged. Do not create a broad all-port forward and do not change
public DNS. The initial installation backup is
`pre-ceph-edge-20260808-0040` (both RouterOS export and system backup). Install
the restricted-node-6dc3f140f9 files with:

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

On restricted-node-6dc3f140f9, create `/dev/ubuntu-vg/ceph_pilot_osd` with 20 GiB from free VG space.
On A1 and B1, run `prepare-wsl-ceph-vhd.ps1` as Windows Administrator to create
`D:\RE8CH-Ceph\ceph-pilot-osd.vhdx` and attach it with
`wsl --mount --vhd --bare`. Install/enable `rook-ceph-wsl-vhd.service` so the
disk is attached before K3s after a WSL or Windows restart. Record the resulting
Linux device and serial before updating `cluster-values.yaml`.

The currently recorded pilot device identities are:

- A1: `/dev/disk/by-id/scsi-360022480b66b60a8f0c873bb147edef8`
- B1: `/dev/disk/by-id/scsi-36002248092fe886403f06a91bb13700e`
- restricted-node-6dc3f140f9: `/dev/ubuntu-vg/ceph_pilot_osd`

Always confirm the three targets are new and contain no filesystem signature
before installing Rook.

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

The Ceph cluster requires messenger v2 but does not enable Ceph wire
encryption. Therefore `csi-drivers-values.yaml` sets the kernel CephFS option
`ms_mode=crc`. Without it, the kernel client can send a v1 banner to port 3300,
which produces a misleading `no mds` mount error even when the MDS is healthy.

## Install

```sh
kubectl label node restricted-node-6dc3f140f9 restricted-node-1eb0570f74 restricted-node-296f36f2fb storage.re8ch.com/ceph-pilot=true
helm repo add rook-release https://charts.rook.io/release
helm upgrade --install rook-ceph rook-release/rook-ceph \
  --version v1.20.3 --namespace rook-ceph --create-namespace \
  -f operator-values.yaml
helm repo add ceph-csi-operator https://ceph.github.io/ceph-csi-operator
helm upgrade --install ceph-csi-drivers \
  ceph-csi-operator/ceph-csi-drivers \
  --version 1.0.4 --namespace rook-ceph \
  -f csi-drivers-values.yaml
helm upgrade --install rook-ceph-cluster rook-release/rook-ceph-cluster \
  --version v1.20.3 --namespace rook-ceph \
  -f cluster-values.yaml
```

Do not install the cluster chart until the operator deployment and discovery
pods are healthy.

## Acceptance

1. Three MONs are in quorum and exactly three OSDs are `up` and `in`.
2. `ceph status` is `HEALTH_OK` before the smoke workload starts.
3. `cephfs-pilot` exists and `local-path` remains the only default class.
4. The smoke DaemonSet has one pod on each pilot node and all three files are
   visible from every pod.
5. Existing Qwen pods and public smoke endpoints remain healthy.

The smoke namespace is disposable, but deleting its retained PV does not erase
the CephFS subvolume automatically. Perform that cleanup only after explicitly
confirming it contains no needed data.
