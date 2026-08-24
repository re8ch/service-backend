# Control-plane API fabric rollout

`192.0.2.116/32` is the K3s fixed registration VIP. It belongs to the host
loopback/FRR accelerated domain and must never be allocated by Cilium LB IPAM.
restricted-node-6dc3f140f9, A1 and B1 are eligible anycast origins, but a node may originate the
prefix only while its local `https://127.0.0.1:6443/readyz` check is healthy.

The checked-in server configs describe the final state. They are host-managed
files, not resources that Flux applies automatically. Never copy all three at
once. Execute the following gates in order:

1. Require all three Nodes Ready, every private WireGuard/BGP session
   Established, and no continuing etcd raft/snapshot catch-up messages.
2. Take and list a fresh etcd snapshot. Record `etcdctl endpoint status` and
   `member list` from all three voters.
3. Deploy Advanced Fabric in `observe-only` mode. Require three consecutive
   healthy observations from each eligible node; no VIP or FRR mutation is
   permitted in this phase.
4. Enable a checksum-bound guarded transaction on one follower. Add the VIP to
   loopback and advertise only `192.0.2.116/32`; verify TLS and withdrawal before
   enabling the other two origins.
5. Roll the K3s server configs one follower at a time and the leader last.
   Confirm the Kubernetes EndpointSlice changes to the corresponding
   `10.250.0.x` after every restart.
6. Update each existing etcd member peer URL with `etcdctl member update` one
   member at a time. Listening on a new address alone does not update stored
   membership. Wait for full index convergence before continuing.
7. Move one worker bootstrap to `https://192.0.2.116:6443`, then vm/zan, then the
   remaining agents. Change internal DNS only after the canary succeeds.

Rollback stops at the current member. Restore its previous config and member
peer URL, then confirm quorum before any other action. Do not remove the
`10.181.22.x` listeners or vm/zan MTU workaround during this rollout.

The rollout is blocked whenever A1 is NotReady, any etcd member is catching up,
or a protected aggregate (`192.0.2.20/24` or `192.0.2.57/16`) appears in FRR.

During the 2026-08-24 A1 recovery, Spegel repeatedly rejected malformed legacy
ToolHive image metadata and consumed a full CPU while kubelet remained
NotReady. Install `a1-disable-embedded-registry.yaml` as an A1-only K3s config
drop-in before restarting that voter. Do not delete containerd content to fix
this condition. Remove the override only after the malformed image reference is
absent and an offline `ctr images check` succeeds.
