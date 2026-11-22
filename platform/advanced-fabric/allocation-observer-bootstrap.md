# Allocation observation bootstrap

`allocation-observer-rbac.yaml` is a one-time platform bootstrap projection of
advanced-fabrics/core PR113 (merged source
`56c40633502279c7413abdb46ed8c899afc2ec42`). It preserves the existing
`advanced-fabric` ClusterRole and all current rules, adding only CiliumNode
GET/LIST for the optional allocation adapter. The existing role binding is
unchanged. No additional identity, RoleBinding, Secret access, Cilium/network
write, bind, escalate or impersonation permission is added.

After signed PR checks pass, preview this exact complete role through the
Admin platform bootstrap API, inspect the server dry-run, and apply with its
exact plan hash. Read back the effective role and compare the preexisting rules.
Do not broaden the networking release identity. Then enable the adapter in the
networking GitOps chart values and verify source UID/resourceVersion and CIDRs
in AdvancedFabric/NPA and peer evidence. Failed reads must remain Unknown.

The public product chart remains the RBAC definition source; this file is its
reviewable initial bootstrap projection, not another reconciler. Runtime chart
and network configuration remain owned by `pillars-infra/network-gitops`. This
file is separate from the historical Helm release in this directory: never
reconcile that obsolete release or restore a Flux writer to install permissions.
