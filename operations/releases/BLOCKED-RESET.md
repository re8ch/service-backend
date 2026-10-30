# Recovering a blocked main-domain release

The executor already supports reset, but previously rejected blocked state before
that action reached its implementation. Reset now bypasses only the blocked and
suspended preconditions. It preserves suspended state, accepted artifact,
healthy digest and inventory. All schema, validity window, sequence,
previous-digest, target, contract, provenance, payload and signature checks remain.
Replay-only and bounded runtime identities cannot reset. Force-conflict requests
are rejected. Reset changes durable state only; it does not apply Kubernetes
workloads or prove their health.

The existing main-domain-release workflow exposes mode reset. It requires both
reuse_digest and reuse_source_commit, verifies the reviewed current source and
contract against that certified commit, verifies the immutable bundle signature,
and materializes a reset operation using the existing publisher identity and
protected Environment. No new bundle is built. No direct state edit, ruleset
bypass, new orchestration service or alternative credential is needed.

## Reviewed execution plan (not executed by this change)

1. Inspect the blocked accepted-state and failed rollout, retaining evidence of
   sequence, applied/healthy digest, inventory and block reason. Resolve the
   runtime prerequisites independently; reset itself does not repair them.
2. Select the exact already signed bundle and certified source commit matching
   the reviewed current contract/source. Review the workflow revision and target
   Environment. Never substitute a caller-selected unsigned artifact.
3. Dispatch main-domain-release on main with the exact contract, mode reset,
   reuse_digest, reuse_source_commit, reviewed_takeover=false and execute=true.
   The normal Environment policy and publisher OIDC authority still apply.
4. Require the executor reset result and durable ConfigMap readback: sequence
   advanced once, blocked=false, reason removed, artifact/inventory and any
   suspension unchanged. The workflow persists state with its existing
   resourceVersion-bound patch. A concurrent update must fail rather than
   overwrite state. A failed attempt is investigated, not manually cleared.
5. In a separate reviewed publish/reconcile operation, perform the intended
   deployment and verify actual runtime readiness. A healthy reset receipt is
   administrative completion, not proof of workload recovery. Keep the original
   failed rollout evidence until successful reconciliation is verified.

Validation covers successful blocked+suspended reset without workload commands,
signature execution, idempotency and unchanged accepted identity; negative cases
include sequence, previous digest, target, contract, audience, workflow, expiry,
payload, force requests and signature failure. Existing replay-only tests reject
reset; ordinary reconciliation remains blocked.
