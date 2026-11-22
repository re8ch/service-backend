# Allocation observation bootstrap

`allocation-observer-rbac.yaml` is platform-owned supplemental read access for
the optional Advanced Fabrics Cilium multi-pool observation adapter. It grants
only CiliumNode GET/LIST to the existing `kube-system/advanced-fabric` service
account. It grants no Secret, Cilium write, route, bind, escalate, impersonation
or node lifecycle capability. Do not broaden the networking release identity.

Apply these exact reviewed objects through the authenticated platform bootstrap
path after signed PR checks pass; preview each complete object and use its exact
plan hash for apply. Read back the effective objects. Then enable the adapter in
the networking GitOps chart values, verify allocation source UID/resourceVersion
and CIDRs in AdvancedFabric status and host peer observations, and perform the
isolated allocation-change acceptance. A failed read must remain Unknown.

This file is deliberately a bootstrap input, separate from the historical Helm
release in this directory. Do not reconcile that obsolete release to install
these permissions. The runtime chart and all network configuration remain owned
by `pillars-infra/network-gitops`. No Flux writer is introduced or restarted.
