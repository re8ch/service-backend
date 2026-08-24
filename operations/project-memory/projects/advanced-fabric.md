# Advanced Fabric project memory

Advanced Fabric is the canonical RE8CH cluster networking architecture and Helm
package. It unifies native Cilium routing, FRR/BGP/BFD, PodCIDR and Service VIP
authority, per-node ECMP observation, and traffic-economics policy.

## Forward-convergence rule

The rollout is deliberately asymmetric and complex. A node-specific failure is
evidence to diagnose that node, not evidence that the architecture should be
globally reverted. Keep accepted native-routing and fabric desired state in
place, mark unhealthy paths ineligible, and repair them forward.

Only initiate a global rollback for verified etcd/API quorum danger, data-loss
risk, or an uncontainable cluster-wide outage. Record the exact trigger and the
last known good revision before doing so.

## Operational sequence

1. Apply an observe-only or guarded desired state in incremental waves.
2. Publish actual per-node evidence: datapath, FRR, BGP/BFD and kernel ECMP.
3. Quarantine failed paths through eligibility rather than deleting topology.
4. Fix individual nodes and verify bidirectional Pod, Service and Gateway paths.
5. Advance the same architecture revision; do not alternate between competing
   global network modes as an experiment.

The Headlamp Advanced Fabric plugin is the primary human view of this evidence.
