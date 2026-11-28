# Storage GitOps inventory bootstrap

> **Historical migration record.** This document preserves a point-in-time
> transition and is not current operating guidance. Local and autonomous Flux
> writers are retired; GitHub Actions and the bounded cloud SSA executor are the
> only platform delivery writer. Do not execute Flux bootstrap, suspend, resume
> or recovery steps from this document. Use
> [`operations/releases/DOMAIN-RECONCILIATION.md`](https://github.com/re8ch/service-backend/blob/main/operations/releases/DOMAIN-RECONCILIATION.md)
> for the current authority and delivery model.


The reviewed destination repository is
`strata-silica/storage-gitops`. Its initial authority declaration remains in
the `inventory` phase with `clusterMutationEnabled: false`.

GitHub-native controls verified during bootstrap:

- active `storage-gitops-main` branch ruleset;
- signed commits, pull requests and the `authority` status check are required;
- `strata-silica/storage-gitops` is selected in the enterprise `GitOps R640`
  runner group;
- five storage delivery Environments exist, are restricted to `main`, require
  the current reviewer, and contain no secrets;
- R640 completed the offline authority validation successfully.

This change records an inventory destination only. The `data-storage`
execution and replay authority, protected Environments, OIDC audiences,
accepted state, Flux sources and SSA field managers remain in
`re8ch/service-backend`. No Kubernetes write or dual-writer condition is
introduced.
