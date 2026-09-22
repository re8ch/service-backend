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

## Dynamic node capacity window

The Penrose leader evaluates every Ready node once per minute. It combines
fresh node-exporter filesystem space and inode counts, memory availability,
five-minute CPU utilisation, Kubernetes pressure conditions, and scheduled Pod
requests against Node allocatable CPU, memory, ephemeral storage and Pod slots.
Only the host filesystems backing `/`, kubelet and containerd affect the node
window. Ceph OSD raw devices and PVC mounts stay under Rook/Ceph and PVC
capacity controls; they are not counted as independent host free space.

Below 15% or 5 GiB available disk, 15% free inodes, 10% or 512 MiB available
memory, 95% CPU utilisation, or high request/Pod occupancy, the window closes.
Recovery requires 20% or 10 GiB disk, 20% free inodes, 20% or 1 GiB memory,
below 80% CPU and lower request occupancy. A closed window adds the owned
`scheduling.re8ch.com/capacity-window=closed:NoSchedule` taint. This stops new
ordinary Pods on either scheduler without evicting running Pods. Penrose and
the node exporter tolerate this exact taint so they can restore observation.

`scheduling.re8ch.com/capacity-window` is `open`, `closed` or `unknown` on the
Node; `capacity-reasons` gives bounded evidence in Headlamp. PlacementClass
and Kueue require `open`. Missing telemetry, including the first sample on a
newly Ready node, closes its window until a fresh open sample arrives. A VictoriaMetrics query
outage leaves the last taint in place. Review Pending Pods, node taints, exporter
freshness and Rook health before manual intervention; do not remove the taint
or delete storage content solely to clear a capacity alert.

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

`PlacementClass` is the workload-facing placement contract. A Pod template
selects one with `scheduling.re8ch.com/placement-class`; the projector turns
reviewed inventory facts into bounded labels and the Kueue admission check
applies the same selector to queued PodSets. Hostname pinning remains native
only for documented locality or public-delivery dependencies and must carry
`pinned-reason`.

## Default and dynamic cohorts

- `default-scheduler` remains the read-only K3s/upstream baseline.
- `re8ch-dynamic-scheduler` is paused at zero replicas. It remains a reviewed
  Trimaran canary recipe and may only be revived for explicitly marked
  workloads after metrics coverage and rollback are proven.

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
3. Keep scheduler-plugins disabled and its lease isolated as
   `re8ch-dynamic-scheduler`; never replace `default-scheduler`.
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
