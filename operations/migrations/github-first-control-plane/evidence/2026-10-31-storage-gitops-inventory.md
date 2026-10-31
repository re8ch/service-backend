# Storage GitOps inventory bootstrap

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
