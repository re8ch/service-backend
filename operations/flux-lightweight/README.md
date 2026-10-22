# Flux lightweight control-plane inventory

This directory is the static discovery boundary for reducing cluster-resident
GitOps work. It does not change the cluster and contains no credentials.

The first migration rule is: **inventory before replacement**. A Flux
controller may be reduced or removed only after every declared CR is mapped to
its controller, dependencies, dynamic behavior, replacement and rollback
owner.

## Files

- `controller-capabilities.yaml` maps Flux API kinds to controllers and records
  which responsibilities are staticizable. Active-controller semantics are
  anchored to the controller tags bundled by Flux v2.6.1, not upstream `main`.
- `executor-operations.yaml` defines the only operations a future external
  executor may perform through a directly reachable Kubernetes API.
- `inventory.py` scans repository YAML and reports the Flux objects that are
  actually declared.

Run the inventory without cluster access:

```sh
python operations/flux-lightweight/inventory.py --format summary
python operations/flux-lightweight/inventory.py --format json > /tmp/flux-inventory.json
```

The output is evidence, not desired state. Generated reports should not be
committed unless a later schema explicitly makes one authoritative.

The version mapping comes from the official Flux v2.6.1 release
`install.yaml`. Source references in the capability catalog point to the exact
controller tag. Future changes must update version and source evidence
together.

## Public Kubernetes API boundary

“Directly reachable” means the API endpoint remains reachable when cluster DNS,
CNI, Gateway API and in-cluster proxies are unavailable. It does not mean
anonymous, unrestricted, or dependent on a committed kubeconfig.

The endpoint design must provide:

- a provider/router-owned TCP path independent of Kubernetes workloads;
- TLS server identity and explicit source/rate controls;
- short-lived client identity where possible;
- separate observer, reconciler and emergency-admin RBAC;
- Kubernetes audit logging and correlation to a GitHub change/operation ID;
- a tested break-glass path whose credential is stored outside GitHub;
- no replacement of the declared R640 public HTTP authority or ad-hoc host
  routes.

`executor-operations.yaml` is intentionally command-free. An executor accepts
a typed operation and allowlisted object identity, never arbitrary shell or raw
`kubectl` arguments.

