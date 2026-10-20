# Control-plane API fabric rollout

`10.250.0.1/32` is the K3s fixed registration VIP. It belongs to the host
loopback/FRR accelerated domain and must never be allocated by Cilium LB IPAM.
R640 and A1 are the production anycast origins. A1 serves the sole K3s API;
R640 serves the local HAProxy relay whose only enabled backend is A1. B1 is a
manual DR target and must not originate or receive VIP traffic before promotion.
A node may originate the prefix only while its local
`https://127.0.0.1:6443/readyz` check is healthy.

The checked-in configs are host-managed files, not resources that Flux applies
automatically. Execute the following gates in order:

1. Require A1 Ready, every required private WireGuard/BGP session Established,
   and no continuing etcd raft/snapshot catch-up messages.
2. Take and list a fresh etcd snapshot. Record `etcdctl endpoint status` and
   `member list` from all current voters.
3. Deploy Advanced Fabric in `observe-only` mode. Require three consecutive
   healthy observations from each eligible node; no VIP or FRR mutation is
   permitted in this phase.
4. Enable a checksum-bound guarded transaction on one follower. Add the VIP to
   loopback and advertise only `10.250.0.1/32`; verify TLS and withdrawal before
   enabling the other two origins.

For the A1 network/TLS canary, install `a1-api-vip-tls-canary.yaml` as the
documented host drop-in before restarting that follower. It changes only TLS
SAN coverage; it must not be used as a substitute for the later advertise or
etcd migration.

### 2026-08-24 A1 canary result

- With R640's `10.181.22.147/32` WG route withdrawn, a temporary route through
  `192.168.88.1` (the MikroTik-managed ZT transport) carried ICMP and TCP
  22/2379/2380/6443 successfully. The API and three-member control plane stayed
  healthy, and restoration selected `wg-pt-a1` again.
- A1 originated `10.250.0.1/32` over its WG BGP session. R640 installed the
  exact route through `172.31.93.147`, and an authenticated kubectl request to
  the VIP returned `/readyz` successfully with normal CA verification.
- Withdrawal removed the VIP from R640's BGP RIB and kernel FIB. The canary VIP,
  FRR network statement and temporary WG AllowedIPs entry were then removed.

The experiment proves both transports and the VIP datapath, but does not make
failover automatic. Before leaving the canary enabled, GitOps must persist a
high-cost MikroTik/ZT fallback route for each control-plane identity and include
the VIP in the eligible WG peers' AllowedIPs. The guarded transaction must bind
and advertise the VIP only while the local authenticated API health check is
passing; observe-only must leave no VIP state behind.

The single-control-plane guarded state enables A1 and the R640 relay. It installs R640's metric-500
fallback routes for A1/B1 through `192.168.88.1` and adds the VIP to the
declared single-peer WireGuard interfaces. Every node verifies the controller's
canonical SHA-256 transaction before mutation. A1 requires three authenticated
local `/readyz` successes to bind/advertise and withdraws after three failures.
R640 applies the same local readiness gate to its relay; B1 remains ineligible.
5. Follow `operations/network/docs/a1-single-control-plane-cutover.md` to reset
   membership to A1 and convert B1 to an agent.
6. Move one worker bootstrap to `https://10.250.0.1:6443`, then vm/zan, then the
   remaining agents. Change internal DNS only after the canary succeeds.

Rollback stops at the current member. Restore its previous config and member
peer URL, then confirm quorum before any other action. Do not remove the
`10.181.22.x` listeners or vm/zan MTU workaround during this rollout.

The rollout is blocked whenever A1 is NotReady, any etcd member is catching up,
or a protected aggregate (`10.181.22.0/24` or `10.42.0.0/16`) appears in FRR.

## A1/B1 WSL recovery contract

A1 and B1 must be started by their Windows Task Scheduler definitions. Those
tasks carry the required WSL arguments and startup ordering; an interactive,
argument-free `wsl.exe` launch is not an equivalent recovery path.

If either WSL control-plane node develops PLEG/containerd initialization
failures after a manual WSL restart:

1. Do not repeatedly restart K3s, delete containerd state, or manually launch
   the distribution again.
2. Reboot the Windows host and allow the existing scheduled task to start WSL.
3. Verify the scheduled task's last result and command line before changing it.
4. Require the node to be `Ready`, CRI `RuntimeReady` and `NetworkReady`, K3s
   and FRR active, its WireGuard BGP session `Established`, and etcd/API healthy.

On 2026-08-24, launching A1's WSL directly left containerd with zero live tasks,
many exited CRI records and stale empty `cri-containerd-*.scope` cgroups; PLEG
never became healthy. A Windows host reboot followed by the existing scheduled
task restored the node without changing the task definition. Treat this symptom
set as a startup-contract failure before investigating the cluster network.

During the 2026-08-24 A1 recovery, Spegel repeatedly rejected malformed legacy
ToolHive image metadata and consumed a full CPU while kubelet remained
NotReady. Install `a1-disable-embedded-registry.yaml` as an A1-only K3s config
drop-in before restarting that voter. Do not delete containerd content to fix
this condition. Remove the override only after the malformed image reference is
absent and an offline `ctr images check` succeeds.
