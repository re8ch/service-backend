# Temporary recovery network-policy exception

NetworkPolicy permits are additive. The retained namespace-wide `allow-egress`
and `allow-scraping` policies in `flux-system` also selected the recovery Pods;
therefore the task Cilium policy alone did not establish deny-all/API-only access.
The initial no-key preflight proves tool availability only, not network isolation.
No age key or recovery ServiceAccount was mounted during that preflight.

The current authoritative source is
`clusters/re8ch/flux-system/gotk-components.yaml`. Both live policy specs matched
that file at inspection. Do not replay this generated file or restart its retired
controllers. The reviewed temporary exception changes only the two existing
`spec.podSelector` fields to exclude these exact app labels:

- `infra-contracts-recovery`
- `infra-contracts-recovery-preflight`

The selector is `app NotIn [these two values]`. Pods without an app label and
Pods with any other value retain their existing allow-policy matching. Rule bodies,
namespace, metadata and other policies are not modified. The normal Git source
continues to describe the original post-recovery state.

## Plan, open, verify, close

1. Through the platform, read the two live NetworkPolicy objects (metadata and
   specs only). Save the snapshot outside Git. Run
   `python operations/infrastructure-contract/recovery/network_window.py open SNAPSHOT`
   to produce a reviewable plan. The planner requires both exact source specs and
   records their UIDs and source-spec hash in a non-secret receipt. Do not commit
   runtime snapshots, UIDs or resourceVersions to the public repository.
2. Review each JSON patch, server-side dry-run it, then use the same reviewed
   patch through the platform. Each tests UID, resourceVersion and the entire spec
   before replacing only the selector. No wildcard update or force takeover.
   Preserve the receipt from this plan. The two patches are not atomic: if either
   fails, keep all key-bearing Jobs stopped and inspect before proceeding.
3. Read back both specs and verify exact excluded labels, unchanged rule bodies,
   and unchanged UIDs. Check all namespace NetworkPolicy/CiliumNetworkPolicy and
   applicable clusterwide policies for any other selector that permits task Pods.
   Check the task's Cilium policy is realized before trusting network isolation.
   Recheck the exception immediately before mounting the age key. The no-key
   preflight's earlier success is not evidence that these network gates passed.
4. Run the separately reviewed recovery phases only once those checks pass.
   After recovery (or on failure), delete all key-bearing recovery Jobs/Pods and
   wait for their removal **before** restoring broad namespace policy matching.
   Remove temporary RBAC and task policies when no recovery workload remains.
5. Read fresh live policy metadata/specs and run
   `python operations/infrastructure-contract/recovery/network_window.py close SNAPSHOT RECEIPT`.
   The planner requires the same UIDs and source hash, and the expected opened spec.
   It emits fresh resourceVersion/whole-spec CAS patches restoring only the source
   selector. A policy already at the original spec is a no-op, allowing explicit
   cleanup after a partial open. Any other change or recreated object rejects.
6. Review, server-dry-run and apply each cleanup patch through the platform, then
   read back exact source spec equality. Retain operation audit references and the
   receipt in the task. No ongoing reconciler or second network authority is added.
