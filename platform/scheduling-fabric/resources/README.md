# RE8CH Scheduling Fabric

This directory is the reviewed desired state for node provenance, workload
placement intent and the RE8CH availability/utilisation triangle. It closes the
gap between the live scheduler-plugins/Trimaran trial, OpenCost, network cost
catalogues and Kubernetes Dynamic Resource Allocation (DRA).

This directory is included by the RE8CH root Kustomization. Promotion remains
staged: the upstream K3s scheduler stays authoritative, the dynamic scheduler
has no production consumers until its metrics gate is healthy, and every
eviction policy starts in dry-run mode.

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

## Capacity is an admission boundary

The platform-wide capacity rule is simple: **no matching available CPU or
memory means no placement**. The Pod remains `Pending` until a Ready,
schedulable node that satisfies all of its selectors, affinities, taints,
claims, CPU request and memory request exists. This is deliberate backpressure,
not an incident to work around.

Every admitted Pod, including platform and control-plane Pods, is mutated and
validated with `spec.preemptionPolicy: Never`. No scheduler, remediation
controller or rollout may make room by preempting a lower-priority Pod,
weakening a node selector or affinity, adding a toleration, removing a taint,
or moving the workload to a node outside its declared constraints. Capacity
must be added, a workload's reviewed resource request must be reduced, or the
workload must wait. `NotReady` is a node-health condition and is investigated
separately; it is never a reason to override this boundary.

## Promotion gates

1. Apply `crds.yaml`, then `node-profiles.yaml`; validate every profile against
   the live Node and existing node/network cost catalogues.
2. Run the two-replica leader-elected observer/projector. It owns only
   `scheduling.re8ch.com/*` labels, CR status, automatic policies/windows and
   explicitly approved operations.
3. Keep scheduler-plugins and its lease isolated as
   `re8ch-dynamic-scheduler`; never replace `default-scheduler`. Three replicas
   must occupy distinct nodes and failure domains.
4. Compare identical canary Services under one `comparisonGroup` for at least
   24 hours. Pareto is computed inside that group only.
5. Expand cohorts only after 100% Trimaran coverage and rollback proof.
6. Keep Descheduler `--dry-run=true` for at least 24 hours. Enabling evictions
   is a separate reviewed revision and is capped per node, namespace and run.

## Control-plane isolation

`NodeProfile.spec.reservationProfile` selects one of the reviewed kubelet
reservation profiles in `node-reservation-profiles`. The controller projects
the selection as `scheduling.re8ch.com/node-profile`; it never changes host
configuration or restarts K3s.

Control-plane isolation is intentionally two-step. Operators first apply
`PreferNoSchedule`, verify that ordinary workloads have moved and that system
components have explicit tolerations, then promote to `NoSchedule`. This
program never uses `NoExecute`. The restricted-node-6dc3f140f9 AmongClusters Gateway remains pinned
and tolerated because it owns the reviewed RouterOS-delivered public listener;
the Hub and Agent run on a stable worker.

The current live scheduler trial is evidence, not authoritative desired state.
Its configuration must be exported without generated metadata and reviewed
before this directory is added to Flux.
