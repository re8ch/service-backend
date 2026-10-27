# RE8CH network fabric model

`model.json` contains a pinned restricted reference for the public WireGuard mesh, alongside the A1/B1
private transit mesh, PodCIDR routing, RouterOS control links, and the AdvFab
path policy. It compiles to the nine existing operations and cluster inventory
files, plus the host-network contract published through Flux. Resolve the protected source as described in [topology custody](../../../contracts/network-topology/README.md) before private validation or rendering. Then run:

```sh
python3 operations/network/fabric/fabric_model.py validate
python3 operations/network/fabric/fabric_model.py render --output-root /private/new-rendered-directory
python3 operations/network/fabric/fabric_model.py check
```

Public CI checks the immutable references and encrypted projection structure without exposing production inputs. The protected `scope=topology` workflow performs full semantic validation and production-fixture tests. `render` writes only to a new private directory outside the checkout; it never overwrites public references with plaintext. `runtimeProjections`
records current operations/runtime differences explicitly; remove a projection
only when its runtime rollout is verified. Do not put WireGuard private keys,
RouterOS credentials, or decrypted Kubernetes Secrets in the model.

`routeIntent.controlPodDirectPairs`, `controlPodFallbackTable`, and
`controlPodRuleBase` compile into the RouterOS control-mesh inventory consumed
by the host's `re8ch-control-pod-mesh` service. The service no longer chooses
A1/B1 versus MUBAO PodCIDR paths from its own hardcoded node list.

The model currently records these verified route responsibilities:

- R640 uses B1's FRR PodCIDR route, with A1 as a tested backup. Its historical
  RouterOS metric-10 route is removed by the PodCIDR reconciler.
- A1 and B1 use their direct interconnect for each other's PodCIDR; RouterOS
  remains a fallback in a separate policy table.
- MUBAO PodCIDR traffic to A1/B1 and their return traffic to MUBAO still use
  RouterOS. The apparent FRR alternatives through R640 failed remote DNS
  probes and must not be promoted until forwarding and return paths pass.
- FLK and LA2 are the two public spines. MUBAO dials both. A1/B1 already dial
  them through private transit. AdvFab's worker policy uses weighted per-flow
  A1/B1 paths to the spine identities; it does not spray packets of one flow.

Host rollout is deliberately separate from generating desired state: compare
the generated configuration with live WireGuard, FRR, RouterOS, and policy
agent state; apply one node at a time; run cross-node DNS and return-path probes
before advancing. The model validation does not imply a live link is healthy.

Host WireGuard/FRR ownership, node plans, promotion and rollback requirements
are standardized in [HOST-NETWORK.md](HOST-NETWORK.md). Generate a node plan
with `fabric_model.py host-plan --node <kubernetes-node-name>`. The GitOps
contract is inventory-only until a transactional host controller is implemented.
