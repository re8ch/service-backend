# RE8CH network fabric model

`model.json` is the reviewed source for the public WireGuard mesh, the A1/B1
private transit mesh, PodCIDR routing, RouterOS control links, and the AdvFab
path policy. It compiles to the nine existing operations and cluster inventory
files. Edit the model, then run:

```sh
python3 operations/network/fabric/fabric_model.py validate
python3 operations/network/fabric/fabric_model.py render
python3 operations/network/fabric/fabric_model.py check
```

CI runs `check` on every pull request. A direct edit to one generated consumer
fails until the same decision is represented in the model. `runtimeProjections`
records current operations/runtime differences explicitly; remove a projection
only when its runtime rollout is verified. Do not put WireGuard private keys,
RouterOS credentials, or decrypted Kubernetes Secrets in the model.

The model currently records these verified route responsibilities:

- restricted-node-6dc3f140f9 uses B1's FRR PodCIDR route, with A1 as a tested backup. Its historical
  RouterOS metric-10 route is removed by the PodCIDR reconciler.
- A1 and B1 use their direct interconnect for each other's PodCIDR; RouterOS
  remains a fallback in a separate policy table.
- MUBAO PodCIDR traffic to A1/B1 and their return traffic to MUBAO still use
  RouterOS. The apparent FRR alternatives through restricted-node-6dc3f140f9 failed remote DNS
  probes and must not be promoted until forwarding and return paths pass.
- FLK and LA2 are the two public spines. MUBAO dials both. A1/B1 already dial
  them through private transit. AdvFab's worker policy uses weighted per-flow
  A1/B1 paths to the spine identities; it does not spray packets of one flow.

Host rollout is deliberately separate from generating desired state: compare
the generated configuration with live WireGuard, FRR, RouterOS, and policy
agent state; apply one node at a time; run cross-node DNS and return-path probes
before advancing. The model validation does not imply a live link is healthy.
