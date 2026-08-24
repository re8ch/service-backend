# RE8CH Platform Inventory

This directory is the canonical, credential-free platform registry.

- `live/` is generated from the Kubernetes API. Do not edit it by hand.
- `governance/` contains reviewed ownership, access and operation policy.
- `generated/` contains human-readable merged views.

The collector records long-running controllers (`Deployment`, `StatefulSet`,
and `DaemonSet`) plus `Service` and `Ingress`. Pods are runtime evidence only;
Jobs and CronJobs are intentionally excluded. Secret values, workload
environment variables, commands, arguments and annotations are never emitted.

Run `python3 scripts/platform_inventory.py --kubectl "sudo k3s kubectl"` on
R640. CI uses `--check` to reject stale generated documents.

The timer must use the `platform-inventory-scanner` identity from
`inventory-scanner-rbac.yaml`; do not give its kubeconfig a cluster-admin user.
Keep the write-enabled Git Deploy Key root-owned on R640. The key may push only
the inventory branch; the repository workflow creates the PR with its built-in
`GITHUB_TOKEN`.
