# Cloud synchronization and runtime observation

The existing five-minute observer runs on GitHub-hosted workers. It reads exact
accepted-state ConfigMaps and workload identities derived from current executor
permissions, plus the latest cloud execution results through the Observer App.
It never reads Flux status, runs a local controller, builds a release or applies
Kubernetes objects. GitHub has no native combined view of these release records
and live Kubernetes health; the bounded collector supplies that missing read-only
projection, without introducing another reconciliation service.

`access.py` derives the inventory and GET-only namespaced permissions from the
existing executor declarations. Regenerate both outputs when those declarations
change. Retired GET-only authorities are excluded. No Secret access, unrestricted
ConfigMap listing, pod logs or cluster mutation is authorized.

`collect.py` keeps three concepts separate:

- Synchronization is the most recent recorded application result. Complete apply
  can coexist with failed health; a rollback is not synchronization to the requested
  revision. No claim of fresh field-by-field drift comparison is made.
- Health is a fresh Deployment/StatefulSet/DaemonSet observation, deduplicated per
  domain and object. It includes declared workloads that have not yet been adopted;
  their health is not proof that their cloud release was accepted.
- Observation failures are Unknown. A newer failed/running cloud workflow or an
  unreadable Actions history cannot inherit a previous accepted success. The
  independent latest-operation field describes workflow execution, not every
  release in that domain. Old successful snapshots expire after fifteen minutes.

The public schema-2 snapshot contains only fixed domain IDs, bounded aggregate
counts and timestamps. Names, namespaces, manifests, digests, topology, raw errors
and credentials never enter the public R2 object or Checks. Failed collection
publishes an unavailable envelope; failed upload leaves the old snapshot to expire.
Detailed inspection remains behind the existing authenticated platform APIs.

The existing `re8ch/headlamp-flux-static-github-ops` Worker and viewer consume the
snapshot at `https://flux-status.re8ch.com/`; `/metrics` exposes the same bounded
cloud aggregates. Legacy schema-1 snapshots are Unknown, never current evidence.
The historical `scope=flux` dispatch name is retained as an input alias only.

The cluster ConfigMaps remain durable executor records. They are not controllers
and do not imply that status computation or scheduling runs locally. GitHub
schedule delay remains possible and is visible through snapshot expiry.
