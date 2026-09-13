# RE8CH business namespace cutover

This directory is the GitHub/Flux desired state for workloads migrated from the
legacy business namespaces `ledger`, `neo4j`, `anysite`, and `dataspace`.

The destination mapping is fixed:

| Legacy namespace | Tenant-owned namespace |
| --- | --- |
| `ledger` | `re8ch-ledger` |
| `neo4j` | `re8ch-neo4j` |
| `anysite` | `re8ch-anysite` |
| `dataspace` | `re8ch-dataspace` |

Secret values are deliberately excluded. The following pre-existing opaque
references must be provisioned by the platform secret controller or the
documented break-glass migration procedure before workloads reconcile:

- `re8ch-ledger`: `ledger-wechat-auth`, `harbor-ledger-pull`,
  `harbor-functions-shared-pull`
- `re8ch-neo4j`: `neo4j-auth`, `ledger-casegraph-auth`,
  `ledger-economicsgraph-auth`
- `re8ch-anysite`: `anysite-db-credentials`
- `re8ch-dataspace`: `dataspace-gateway`

The existing `ledger-wechat-auth-agent-token-20260727` ConfigMap is also a
temporary compatibility input. It contains mounted handler source rather than
credentials, but remains outside Flux until that source is packaged into the
immutable function image; Flux manages the Deployment reference without
rewriting the live ConfigMap.

Persistent data was copied only after stopping the corresponding writer. Old
PVCs and zero-scaled workloads remain in the legacy namespaces for the rollback
window. Rollback consists of removing the destination route, scaling the legacy
writer to one, and restoring its route; never mount both RWO data copies as
active writers.

`operations/scripts/export-migrated-business-namespaces.py` can refresh these
manifests from reviewed live state. It excludes Secrets, status, object identity,
generated virtual routes and volume bindings.
