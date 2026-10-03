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
through the existing network PodCIDR OCI domain. This is an inventory-mode
declaration. It starts no privileged agent, changes no route, and does not
authorize existing consumers to apply a host plan. CI rejects switching to
apply mode or listing adopted nodes until an adoption controller is implemented.

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

A future host controller consumes a signed promoted revision plus this model
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

This change standardizes desired configuration and the future controller's
contract. Automatic host deployment, timed local rollback and live drift
reconciliation are separate implementation work and are not activated here.
