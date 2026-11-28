# Cloud SSA domain delivery

> `DOMAIN-RECONCILIATION.md` is retained as a stable link target. Flux domain
> reconciliation is retired. GitHub Actions and the bounded cloud SSA executor
> are the only platform delivery writer; local and autonomous Flux writers must
> not be restored.

GitHub stores reviewed desired state and immutable release evidence. A protected
GitHub Actions workflow resolves the accepted artifact, exchanges OIDC for the
exact cluster identity, and invokes the bounded executor. The executor applies
the approved bundle directly to the named cluster API with a stable SSA field
manager. Kubernetes remains runtime authority.

```text
reviewed declaration
  -> immutable signed bundle
  -> protected GitHub Environment and OIDC
  -> bounded cloud SSA executor
  -> named cluster API
  -> accepted state and apply receipt
  -> independent runtime observation
```

## Authority

[`operations/flux-federation/authority.yaml`](../flux-federation/authority.yaml)
is the machine-readable delivery ledger despite its historical path. Its
normative current fields are:

- `deliveryMode: github-actions-ssa`;
- `objectOwnership: single-active-writer`;
- each delivery domain has `state: github-actions-ssa`;
- `controllerVersion: retired`.

Historical `controllerNamespace`, cutover lock, Flux CR, HelmRelease and
Kustomization records are migration or rollback evidence. They do not identify
a live writer and must not be used as an execution target.

## Domain execution

The delivery domains are `main`, `networking`, `identity-access`,
`agent-runtime`, `observability-metering`, and `delivery-products`. Each domain
has its own repository or reviewed source, protected Environment, OIDC audience,
cluster identity, bounded RBAC, release inventory and durable accepted state.

Cloud SSA writes directly to the cluster named by the ReleaseContract. The main
cluster, PublicEdge and AmongCluster never proxy another cluster's Kubernetes
write. Multi-cluster acceptance follows the
[`multi-cluster federation contract`](../network/docs/multicluster-federation-contract.md).

An accepted artifact is not deployment evidence. Completion requires:

1. the exact reviewed target and executor identity;
2. signature, contract, digest and inventory verification;
3. conflict-detecting server-side apply under the approved field manager;
4. read-back of accepted/applied state from the same cluster;
5. workload and functional health evidence from an independent observer.

## Replay and drift repair

The scheduled workflows with historical `reconcile` names are cloud replay
clients. They do not run a cluster controller, poll Git from Kubernetes or
restore Flux. Replay resolves the already accepted immutable digest and submits
the same bounded operation envelope to the executor.

Observation is read-only and separate from execution. A stale or unavailable
observation becomes `Unknown`; it never inherits an earlier successful result.
Runtime evidence may trigger a reviewed replay or new change, but the observer
is not a writer.

## Product runtime boundary

Externally consumed, fast-changing products may use their authenticated product
release API. Platform guardrails and infrastructure stay in Cloud SSA. Business
state stays in the owning application API. The reviewed split is recorded in
[`runtime-authority.yaml`](runtime-authority.yaml).

No product publisher may acquire Secret values, unrelated cluster-scoped RBAC,
shared Gateway ownership, certificate issuance or database administration.

## Historical material

The following paths are retained only to explain or verify the completed
migration:

- `operations/flux-federation/migrations/`;
- `operations/releases/FLUX-RETIREMENT.md`;
- `operations/migrations/cloud-gitops-cutover/`;
- Flux CR templates and retirement inventories referenced by those records.

Do not execute their suspend, resume, bootstrap or controller-recovery steps.
Restoring a Flux writer would be a new authority transfer and is outside normal
recovery.
