# Host WireGuard/FRR GitOps contract

The `hostNetwork` section of `model.json` defines the common ownership,
delivery, adoption, validation, and rollback contract. `fabric_model.py`
compiles a plan for every declared PodCIDR-fabric host, using Kubernetes node
names even when older WireGuard inventories use aliases. The content digest
identifies the exact model and node plans.

```sh
python3 operations/network/fabric/fabric_model.py host-plan --node restricted-node-abb3f27a69
python3 operations/network/fabric/fabric_model.py check
```

Flux publishes the compiled document as `kube-system/host-network-contract`
through the existing network PodCIDR OCI domain. The current release enables the single scoped canary described below. Other
host plans remain inventory-only. CI rejects fleet-wide adoption.

## Ownership

| Resource | Unit of ownership | Current integration |
| --- | --- | --- |
| WireGuard | interface, peer and AllowedIPs | Public/private mesh inventories and current reconcilers are identified per host. A single assembler must combine their contributions before takeover. |
| FRR | neighbor, prefix-list and route-map | The existing shared `frr.conf` must not be replaced with one generator's partial output. |
| Linux routing | prefix, table and rule priority | FRR, RouterOS fallback and AdvFab table ownership must be resolved before activation. |
| AdvFab | eligible nexthops and policy tables | Weighted per-flow selection operates within FRR reachability; it cannot overwrite underlay/control routes. |
| RouterOS | declared links and prefix policy | Existing public authority remains in the public-mesh source section. |

`declaredWriters` is the list to audit, not proof that a writer is active on a
host. Adoption must capture a live writer inventory and resolve scope conflicts.
The existing podcidr reconciler, wg-quick units, persisted FRR configuration,
control-Pod controller and policy agent remain responsible for their current
runtime state until each scope is explicitly transferred.

## Version and transaction format

The host controller consumes a signed promoted revision plus this model
digest. Each transaction must record node identity, desired digest, baseline
digest, owned files/scopes, validation results, and previous committed revision.
WireGuard private keys stay in approved host/Secret storage and are referenced
by location; configuration packages must not contain private keys.

The state sequence is inventory → staged → validated → applying → verifying
→ committed. A failure enters rolling-back → rolled-back or failed. Only one
node may apply at a time; control-plane nodes must be serialized. Writer
handoff, baseline snapshot and an independent management path are prerequisites.

Before apply, validate WireGuard/FRR syntax, prefix scope, loop prevention and
the restricted-node-6dc3f140f9 public authority. Before commit, verify handshakes, BGP/BFD, both PodCIDR
directions, every DNS replica over UDP/TCP, control-plane VIP and egress proxy.
Pod Ready and BGP Established alone are insufficient.

Rollback must execute on the host even if Kubernetes or the delivery channel
is unavailable. Restore both persistent configuration and runtime state for
the owned WireGuard/FRR/routes/AdvFab scopes from the last known good snapshot.
Do not retry a failed revision indefinitely. Boot from the last known good
revision; retain failure evidence for the next rollout.

The contract covers the intended full ownership model. The implemented v1
adapter supports only the scopes listed below.

## v1 scoped canary controller (2026-10-04)

The controller now supports a single non-control-plane canary: overseas-flk.
Its scope is the public-mesh interface MTU, the restricted-node-6dc3f140f9/LA2 peers' AllowedIPs,
endpoint and keepalive, and their exact FROM-* FRR prefix lists. All other
neighbors, keys, interfaces, route tables and AdvFab policy remain with their
existing owners. The compiler combines public identities and PodCIDR ownership
before emitting each complete peer prefix set. The old PodCIDR reconciler checks
the local adoption manifest and yields these exact peers to the new assembler.

Flux delivers a hashed ConfigMap through the existing signed PodCIDR OCI domain.
The installer starts host systemd recovery/watchdog services before the forward
timer. Node-local immutable desired digests, durable pre-write journals,
three consecutive successful health rounds, bounded workers, reboot recovery,
and failed-revision blocking implement a real transaction. Rollback restores
only adopted runtime and persistent fields; it does not replace shared FRR.
Private keys never enter packages or transaction journals.

Live DNS EndpointSlices are refreshed into a separate timestamped health file.
The host gate rejects discovery older than 120 seconds, missing DNS replicas,
failed UDP/TCP DNS, BGP, BFD, WireGuard handshakes, control VIP/direct API TCP,
or explicit egress proxy tests. An unhealthy baseline blocks forward changes.
Recovery uses only the local journal and works without Kubernetes connectivity.

The first canary has not been certified as globally healthy: initial FLK checks
could reach A1/B1 DNS but timed out to restricted-node-6dc3f140f9/MUBAO DNS. Installation is not a claim
of successful adoption or fleet-wide convergence. Query the host journal and
status files, rather than treating the delivery Pod's Running phase as success.

```sh
systemctl status re8ch-host-network.timer re8ch-host-network-watchdog.timer
cat /var/lib/re8ch/host-network/status.json
# Journal contains public network scopes only, but stays root-readable.
cat /var/lib/re8ch/host-network/transaction.json
```

V1 intentionally rejects additional adopted nodes. Expanding adoption requires
extending the rollout coordinator and reviewing each ownership handoff, including
bidirectional data-plane probes. FRR neighbor lifecycle, route/rule and AdvFab
transactions are not yet supported by this adapter; they must not be marked as
owned by it. Roll back the delivery through GitOps; never remove host rollback
services while a transaction is applying/verifying/rolling-back.
