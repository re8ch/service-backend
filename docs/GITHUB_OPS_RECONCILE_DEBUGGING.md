# GitHub Actions to Kubernetes reconciliation debugging

This is the authoritative runbook for RE8CH GitHub-first reconciliation. Use it
when a repository badge or scheduled reconcile is red even though Git, artifact
verification, or part of the runtime update appears successful.

The delivery path is:

`reviewed main -> GitHub Actions OIDC -> bounded Kubernetes identity -> immutable OCI artifact -> server-side operation -> runtime read-back`

Local Flux controllers are not a recovery path. Retained Flux objects are
migration evidence; do not repair, restart, or grant them authority.

## Diagnose the failing boundary

1. Open the newest run on `main` and inspect jobs, not only the badge.
2. Classify the first failed step: selection, OIDC/TLS, authorization, signature
   or pull, executor/dry-run, health observation, or final aggregation.
3. Record run ID, job ID, repository SHA, release ID, namespace, audience,
   accepted OCI digest, and the first failing step. Never record an OIDC token,
   kubeconfig, App key, installation token, decrypted secret, or SOPS key.
4. Treat a success line as evidence for that line only. For example, successful
   cosign verification, artifact extraction, or ConfigMap patch can be followed
   by a later failing command in the same shell step.

## Identity and TLS

- Log only decoded OIDC timing claims (`iat`, `nbf`, `exp`) and current UTC time.
- Use `kubectl auth whoami` with bounded retries. Verify the exact username and
  Kubernetes group produced by the reviewed workflow/environment/audience.
- An `EOF` from `whoami` is transport or API-edge evidence. A successful
  `whoami` followed by authorization denial is an RBAC or request-shape issue.
- Check the public API with its approved CA and compare it with the R640 control
  plane. Do not disable TLS verification or replace the declared endpoint.

## Authorization

For a positive write proof, prefer the exact operation with no state change:

```sh
kubectl patch configmap "$state_name" -n "$namespace" \
  --type=merge --patch '{}' --dry-run=server -o name
```

`kubectl auth can-i` is still useful for negative boundaries. Its expected `no`
output commonly has process exit status 1; wrappers must treat literal `yes` and
`no` as valid decisions and retry only transport/API errors.

Compare all three surfaces:

- desired Role/ClusterRole and Binding on reviewed `main`;
- `kubectl auth whoami` from the actual Actions OIDC identity;
- live `kubectl auth can-i ... --as-group=<exact-group>` and the target object.

Do not broaden a group or add wildcard permissions. Repair the missing exact
resource name, verb, namespace, or cluster-scoped binding.

## Accepted state, artifact, and executor

- Selection reads only the exact `ssa-state-<release>` ConfigMap. Distinguish
  missing state, invalid state, read timeout, and forbidden read.
- Reuse the accepted immutable digest; do not rebuild it during reconcile.
- Verify signer identity, issuer, OCI digest, bundle metadata, contract digest,
  inventory digest, target, and replay provenance before execution.
- Preserve executor result and independent observation artifacts. Download the
  smallest relevant artifact when logs intentionally sanitize an exception.
- In observe-only workflows, an SSA conflict or unavailable dry-run is degraded
  observation, not permission to force ownership. Publish/apply workflows remain
  fail-closed. Inspect `managedFields` before deciding who owns the drift.

## Dispatch and recovery

Dispatch through the Re8ch Admin Governance singleton
`service://admin/github-enterprise-governance`: preview the workflow dispatch,
then apply the identical request with its `planHash`. The audit reference must
identify the repository and workflow. Do not use a local GitHub token or `gh api`
as the workflow control plane.

Use the R640 fallback only when the normal cloud path cannot restore a reviewed
bootstrap object. Apply the exact object already present on reviewed `main`, run
`--dry-run=server` first, avoid broad `--force-conflicts`, and read back both the
object and impersonated authorization. If a shared Role has newer live entries,
add only the missing reviewed resource name instead of replacing the rule set.
Record the fallback as incident evidence and repair the normal cloud authority.

## Completion evidence

A reconcile is recovered only when the newest run on the intended `main` SHA is
terminal `success`, every selected release job is accounted for, and the actual
runtime read-back agrees with the accepted state. A merged PR, a green helper
job, a patched ConfigMap, or a passing badge by itself is insufficient.

