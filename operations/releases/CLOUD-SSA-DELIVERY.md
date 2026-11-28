# Cloud SSA delivery

GitHub stores reviewed intent. GitHub Actions builds and verifies one immutable
bundle per source revision and build configuration. The bounded cloud SSA
executor is the only platform delivery writer.

## Authority

[`operations/flux-federation/authority.yaml`](../flux-federation/authority.yaml)
is the delivery ledger. Each target declares `deliveryMode:
github-actions-ssa` and `objectOwnership: single-active-writer`.

Kubernetes is runtime authority. A merged declaration, successful build or
accepted grant is not completion evidence.

## Delivery

1. Resolve the reviewed target, contract and immutable artifact digest.
2. Use the protected GitHub Environment for that target.
3. Exchange the workflow identity for the target-specific API identity.
4. Server-side dry-run the exact inventory.
5. Apply through the bounded executor without force ownership takeover.
6. Read back accepted state, managed fields and workload health.
7. Record the operation and runtime evidence on the Issue and PR.

Scheduled replay may resubmit an already accepted immutable bundle. Replay does
not build a new artifact, change ownership or bypass approval.

## Observation

Observers are read-only. They publish bounded delivery results and runtime
health, but never apply resources or repair drift. Unknown or expired evidence
remains Unknown.

## Failure handling

Stop on target, identity, dry-run, ownership, read-back or health failure. Fix
the reviewed declaration or executor contract and run the protected workflow
again. Rollback uses a previously accepted immutable digest through the same
target-specific executor path.

