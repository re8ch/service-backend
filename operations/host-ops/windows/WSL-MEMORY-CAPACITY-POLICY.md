# A1/B1 WSL memory capacity policy

## Authority and limits

Kubernetes capacity is calculated from the memory and CPU that the running WSL
guest actually reports, never from the Windows host's nominal hardware size.
For A1 and B1 the normal Node `Allocatable / Capacity` ratio must be at most
90% for both memory and CPU. A bounded experiment may temporarily exceed 90%,
but must remain below 95%, retain the independent A1-to-B1 or B1-to-A1 Windows
SSH recovery path, and have an explicit rollback. A value at or above 95% is
invalid because kubelet, the Linux kernel and the Windows host would have no
credible recovery margin.

The reviewed normal profile is `wsl-balanced`:

- kube reserved: 500m CPU, 512Mi memory, 1Gi ephemeral storage, 1024 PIDs;
- system reserved: 500m CPU, 512Mi memory, 1Gi ephemeral storage, 1024 PIDs;
- hard memory eviction: 512Mi available;
- maximum Pods: 128.

The kubelet subtracts both reservations and the hard eviction threshold when
publishing memory Allocatable. Operators must read the resulting Node status
after every change; adding the declared reservations alone is not sufficient.

The first live application of this normal profile reported:

- A1: 12,247,684Ki memory Capacity and 10,674,820Ki Allocatable (87.16%);
- B1: 14,312,068Ki memory Capacity and 12,739,204Ki Allocatable (89.01%);
- both hosts: 8 CPU Capacity and 7 CPU Allocatable (87.5%).

## Host-specific reclaim policy

A1 remains the single embedded-etcd control-plane voter. It uses a 12GB WSL
ceiling, 3GB swap, `pageReporting=true` and `autoMemoryReclaim=gradual`.

B1 is the storage coordination and manual DR standby host. Its reviewed
experiment uses a 14GB WSL ceiling, 3GB swap, `pageReporting=false` and
`autoMemoryReclaim=disabled`. This is intentionally not a fleet-wide default.
Disabling reclaim does not preallocate 14GB; the guest must touch memory before
the VMM working set reaches the ceiling.

## B1 high-water experiment — 2026-11-18

The test started with k3s-agent, Cilium, Rook and Ceph MON/MGR/MDS healthy. A
transient systemd unit allocated and touched a 9GiB Python byte array for up to
90 seconds. Observation used the independent A1-to-B1 Windows SSH path so the
test did not depend on B1's guest remaining responsive.

Observed high-water evidence:

- WSL-visible memory: 14,312,068Ki Kubernetes Capacity (about 13.65GiB);
- `vmmem` working set peak: about 13.1GB;
- Windows free physical memory minimum: about 0.17–0.34GB;
- B1 SSH and the independent Windows SSH path remained responsive;
- k3s-agent, Cilium, Rook and Ceph MON/MGR/MDS remained running;
- Linux swap stayed near zero during allocation and was about 12–35MiB after;
- after the allocation exited, Windows recovered to about 2.28GB free while
  `vmmem` remained around 10.94GB, confirming a retained high-water effect.

The temporary allocation unit was stopped and was not enabled persistently.
The prior 12GB and gradual-reclaim `.wslconfig` files remain as timestamped
backups in the B1 Administrator profile for rollback.

## Validation and rollback

After changing kubelet reservations, read `.status.capacity` and
`.status.allocatable` from the live Node and calculate the ratio. Do not infer
success from the systemd arguments alone. Confirm Node Ready, k3s, Cilium and
the Ceph control daemons before completing the operation.

If B1 host SSH becomes slow or unavailable, stop the test allocation through
the A1 jump path. If pressure persists, restore the gradual-reclaim backup,
set `memory=12GB`, stop WSL, and start the reviewed lifecycle task. A1 capacity
changes require a controlled k3s restart and must never change A1 to the B1
non-reclaiming experiment while A1 is the sole control plane.

## Tianyi Cloud power recovery

The official `ctyun-cli` is an execution client, not a secret store or a
reconciliation controller. Automatic power recovery must run on a long-lived
machine outside A1 and B1, such as R640, so simultaneous host power loss does
not remove the recovery actor.

Version 0.4.0 was downloaded from the official Tianyi Cloud release endpoint
and installed without credentials at `/home/r640/.ctyun-cli/bin/ctyun-cli` on
R640. The same version is available on the management Mac at
`/Users/harryneo/.ctyun-cli/bin/ctyun-cli`. These binaries do not authorize any
cloud operation until an approved credential is injected.

Only secret names, purposes and scopes belong in Git. The runtime credential
requires an IAM sub-user AccessKey and SecretKey with read access to the two
target cloud computers and permission to start or resume only those resources.
Depending on the inventory returned by the account, the relevant operations
are expected to be `ecpc DescribeDesktop` plus `ecpc BatchStartDesktop` or
`ecpc BatchResumeDesktop`; compute-enhanced desktops use `DescribeEcs` and
`BatchStartEcs`. Do not grant create, delete, rebuild, resize, password-reset,
image, network or organization-administration permissions.

The non-secret inventory still required before automation is:

- the Tianyi Cloud product type for A1 and B1;
- region/resource-pool ID;
- desktop OID or compute-enhanced desktop OID for each host;
- expected stopped, suspended and running states;
- retry interval, maximum start attempts and notification destination.

The automation must query current state before issuing a start, treat already
running/starting/waking hosts as success, bound retries, and verify Windows SSH
then WSL/k3s independently. It must never log the SecretKey or pass it in a
process command line.
