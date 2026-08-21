# Cilium BGP control-plane performance checkpoint — 2026-08-07

## Scope and isolation

This test concerns only the Kubernetes Cilium BGP control plane. It does not
peer with DN42, import DN42 routes, use the MikroTik `dn42` VRF, or advertise
the DN42 allocation. No `CiliumBGPClusterConfig`, `CiliumBGPPeerConfig`,
`CiliumBGPAdvertisement`, or `CiliumLoadBalancerIPPool` object was created.

## Pre-change baseline

- All 11 nodes were Ready.
- All 11 Cilium agents and both A1/B1 Cilium operators were Ready.
- BGP Control Plane was disabled and all four BGP/LB IPAM resource sets empty.
- Agent use ranged from 11–224 millicores and 138–309 MiB at the sample point.
- Operators used 3/25 millicores and 28/47 MiB at the sample point.
- Kubernetes metrics API was healthy.
- The Shanghai Qwen endpoint returned HTTP 200 when TLS verification was
  bypassed; strict verification already failed because the certificate did not
  match the hostname, so this is a separate pre-existing TLS issue.

## Enablement result

The dormant BGP control-plane flag was set and the Cilium DaemonSet rolled one
node at a time. Ten of eleven replacement agents became Ready. The replacement
agent on `restricted-node-6dc3f140f9` could not start because the local container runtime retained a
five-day-old Cilium sandbox and its exited containers in a stuck `removing`
state. Targeted `crictl rm` calls returned `already in removing state` and a
deadline exceeded error. The same runtime log contains stale sandbox-name
reservations for multiple unrelated restricted-node-6dc3f140f9 workloads, indicating a node-local
containerd/K3s runtime fault rather than a BGP session or route-programming
failure.

Following the cluster safety rule for runtime/API timeouts, the rollout was
stopped and no BGP custom resources or further networking changes were made.
Because there are no BGP resources, the ten updated agents cannot establish a
BGP session or advertise/import any route.

## Required recovery and next measurement

Recover the restricted-node-6dc3f140f9 container runtime in a dedicated maintenance window, then
verify all existing restricted-node-6dc3f140f9 workloads and complete the single pending Cilium
agent. Only after 11/11 agents are Ready should the post-enable CPU, memory,
connectivity, loss, latency, API, Qwen, and BGP-controller metrics be compared
with this baseline. An internal peer/address design must be approved separately
before applying any Cilium BGP or LB IPAM resource.

## Recovery and staged peering result

The user authorized an restricted-node-6dc3f140f9 K3s/containerd maintenance restart. The restart
cleared the stale runtime state and the restricted-node-6dc3f140f9 Cilium agent became Ready, giving
11/11 agents immediately after recovery. The Cilium operators were then
restarted so that they consumed the already-enabled BGP control-plane setting.

An isolated internal BGP domain was created with Cilium ASN 64520 and MikroTik
ASN 64519. It is separate from the RouterOS `dn42` instance and has independent
filter chains. Cilium advertises only its local IPv4 PodCIDR with `no-export`.
MikroTik accepts only `192.0.2.57/16` routes of length `/24`, applies a maximum of
four routes per exact peer, and rejects all exports back toward Cilium.

The following sessions established and installed one route each:

- restricted-node-6dc3f140f9 over the LAN path;
- A1 and B1 over ZeroTier;
- the AMD WSL node over ZeroTier;
- qianwen-prod and qianwen-test over ZeroTier.

This produced six established sessions and six active PodCIDR routes on the
MikroTik. Five nodes with real public addresses were tested first against the
ZeroTier edge identity and then against the restricted public edge on alternate
TCP port 1179 with eBGP multihop. They remained in `active`; MikroTik packet and
firewall counters saw zero incoming SYNs. The block is therefore before the
MikroTik edge, in the node/cloud egress path. Those nodes must not be recorded
as optimized until their host routing/security policy is inspected or a
dedicated tunnel is provided.

During the post-change sample, `vm-20-12-ubuntu` stopped renewing its node lease
and became NotReady. The WireGuard underlay remained alive (about 21 ms and zero
loss from MikroTik), and restricted-node-6dc3f140f9 could reach its kubelet TCP port, but SSH to the
host timed out and kubelet stopped posting status. This reduced Cilium
availability reporting to 10/11 even though its existing agent container still
showed Running. At the same time the Qwen smoke endpoint changed from HTTP 200
to HTTP 503 and metrics-server was found reconciled back to a single unready
replica, so a trustworthy post-BGP CPU/memory and application-latency comparison
could not be completed. No further BGP expansion or LB IPAM change was made.

The RouterOS pre/post exports and system backups are named with
`cilium-bgp-20260807`. The declarative Kubernetes objects are in
`manifests/k3s/cilium/cilium-internal-bgp.yaml`.

## Architecture correction and rollback

The MikroTik design above was subsequently rejected because the router is the
public/NAT edge representing restricted-node-6dc3f140f9, not an independent cluster route reflector.
The Cilium ASN 64520 to MikroTik ASN 64519 sessions, advertisements, generated
node configs, RouterOS BGP instance, dynamic PodCIDR routes, filters, firewall
rules, NAT rule, and address lists were fully removed. The temporary manifest
was deleted from the repository. Cilium BGP Control Plane remains enabled but
has no CRs, peers, or advertisements, leaving it available for a future local
Cilium-to-FRR handoff.

The authoritative accelerated underlay is the existing `wg-public-mesh` plus
per-node FRR. Public-mesh FRR advertises each participating node's stable
`10.181.22.x/32`; those more-specific metric-20 routes enter the Linux FIB and
therefore steer Cilium VXLAN outer traffic over WireGuard while the connected
ZeroTier `/24` remains the fallback.

ICP (`vm-20-12-ubuntu`) had a complete and healthy WireGuard/BFD configuration,
but `/etc/frr/frr.conf` had not been loaded into the running FRR configuration.
After a successful `frr-reload.py --test`, an incremental reload restored all
six eBGP neighbors. Each neighbor became Established with one received and one
sent prefix; all six BFD sessions are Up and six metric-20 `/32` routes are in
the kernel. Remote peers also confirmed ICP Established. The pre-reload FRR
configuration backup is `/etc/frr/frr.conf.pre-icp-publicmesh-fix-20260807`.

Post-change verification showed all 11 Kubernetes nodes Ready, Cilium 11/11,
no Cilium BGP resources, Qwen HTTP 200, and the independent DN42 session still
Established. RouterOS holds pre/post rollback exports and system backups named
with `cilium-bgp-rollback-20260807`.
