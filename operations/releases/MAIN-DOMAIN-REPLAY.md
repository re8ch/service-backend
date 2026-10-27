# Cloud replay of accepted main-domain releases

`main-domain-reconcile.yml` runs after relevant signed-main changes, every half
hour, and on manual dispatch. It has no caller-supplied artifact, mode, contract,
force or rollback input. Its matrix comes from canonical main-domain contracts
whose execution authority declares a replay profile in
`static-domain-authority.yaml`. External domain repositories are excluded.

Publishing remains in the existing approval-protected `main-domain-release`
workflow. Replay uses separate `release-reconciler-<domain>` environments,
main-only deployment policies and exact hosted-worker OIDC identities. Those
identities retain the existing object-scoped executor groups; they do not need
Secrets, pod logs, token creation, impersonation or Flux resource permissions.
The environment retains only the API CA secret name `KUBE_API_CA_PEM`.

The executor requires healthy accepted state, the same immutable OCI reference,
digest, payload and inventory, and an exact contract digest in the signed bundle.
It rejects initial publication, changed contracts, forced ownership takeover,
reset, suspend, resume and rollback through this identity. Original publisher
signatures are still verified. Contract compatibility migrations supported by
manual publishing are deliberately unavailable to unattended replay. A stale
accepted contract must first be published through its protected environment.
Missing acceptance is reported as pending initial publication, without mutation.

Native GitHub schedule, push triggers, matrix jobs, environments and concurrency
provide orchestration; no additional controller or polling service is introduced.
The only added selection logic maps the existing contract registry to its exact
execution profiles. Replay and publication share the same per-contract concurrency
group, and state persistence also checks the original resourceVersion. An active
failure cannot silently erase a blocked state.

Before merging/enabling this workflow, provision the four declared replay
Environments and their exact API-server OIDC routes. Leave existing publish
reviewers unchanged. The schedule is enabled directly, without a default-off
repository variable. GitHub scheduling is best effort, so this is not a real-time
availability controller; Kubernetes workload controllers remain responsible for
runtime health.

Native primitives: https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
