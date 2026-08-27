# RE8CH Scheduling Fabric

This directory is the reviewed desired state for node provenance, workload
placement intent and the RE8CH availability/utilisation triangle. It closes the
gap between the live scheduler-plugins/Trimaran trial, OpenCost, network cost
catalogues and Kubernetes Dynamic Resource Allocation (DRA).

Nothing in this directory is part of a parent Kustomization yet. Promotion is
deliberately staged because the live scheduler currently serves the
`default-scheduler` profile and all three replicas run on `restricted-node-6dc3f140f9`.

## Model

`NodeProfile` is the human-owned, relatively static description of a node. It
records provenance and failure domains, lifecycle/stability, compute, storage,
network and cost capabilities. Measurements remain in their existing systems:
Kubernetes/node-exporter, Cilium, VictoriaMetrics and OpenCost. Controllers
project only safe, low-cardinality scheduling fields to Node labels.

`ServiceOutcomePolicy` gives a Service two explicit states:

- `spec.desired`: the requested Resilience/Efficiency/Stability outcome.
- `status.observation`: the observed point, evidence completeness and window. A controller owns
  status; users and GitOps never write observations into `spec`.

The three coordinates are independent scores in `[0,100]`, not percentages
that must sum to 100. This preserves an important distinction: a service may
have a high target on all three axes, while `constraints` make the trade-offs
and infeasibility visible.

`SchedulerProfile` registers immutable upstream and bounded managed scheduler
cohorts. `DisturbanceWindow` marks natural or manual observation intervals;
it never injects a fault. `PenroseOperation` is the audit/approval boundary for
the limited management actions exposed by Headlamp.

## Default and dynamic cohorts

- `default-scheduler` remains the read-only K3s/upstream baseline.
- `re8ch-dynamic-scheduler` uses Trimaran load variation and
  `NodeResourcesAllocatable` packing, network cost and OpenCost may increase
  utilisation. A canary may use it only when its eligible nodes have fresh CPU
  and memory recording rules.

DRA is an allocation constraint, not another triangle axis. `DeviceClass`,
`ResourceClaim` and `ResourceSlice` participate in scheduler filtering; the
Triangle controller observes claim availability and reports when a desired
failure-domain count is impossible for a node-local device.

## Promotion gates

1. Apply `crds.yaml`, then `node-profiles.yaml`; validate every profile against
   the live Node and existing node/network cost catalogues.
2. Run the two-replica leader-elected observer/projector. It owns only
   `scheduling.re8ch.com/*` labels, CR status, automatic policies/windows and
   explicitly approved operations.
3. Rename scheduler-plugins and its lease to `re8ch-dynamic-scheduler`; never
   replace `default-scheduler`. Spread its replicas by node/failure domain.
4. Compare identical canary Services under one `comparisonGroup` for at least
   24 hours. Pareto is computed inside that group only.
5. Expand cohorts only after 100% Trimaran coverage and rollback proof.

The current live scheduler trial is evidence, not authoritative desired state.
Its configuration must be exported without generated metadata and reviewed
before this directory is added to Flux.
