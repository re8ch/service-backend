# Parameterized Flux-to-static-GitHub-Ops standard

> **Historical migration record.** This document preserves a point-in-time
> transition and is not current operating guidance. Local and autonomous Flux
> writers are retired; GitHub Actions and the bounded cloud SSA executor are the
> only platform delivery writer. Do not execute Flux bootstrap, suspend, resume
> or recovery steps from this document. Use
> [`operations/releases/DOMAIN-RECONCILIATION.md`](https://github.com/re8ch/service-backend/blob/main/operations/releases/DOMAIN-RECONCILIATION.md)
> for the current authority and delivery model.


This is the single onboarding path for all nine domains. Its machine-readable
source is [`operations/releases/static-domain-standard.yaml`](../../releases/static-domain-standard.yaml),
and `static_domain_standard.py` validates the nine-domain authority map and
materializes an exact per-domain plan.

```text
authority parameters
  -> inventory and immutable locks
  -> repository + protected environment + OIDC + exact RBAC
  -> signed shadow bundle
  -> exact-identity server dry-run
  -> suspend one old Flux consumer
  -> bounded SSA takeover
  -> non-force idempotent replay
  -> risk stability window
  -> zero-consumer controller retirement
```

Generate or validate a plan with:

```bash
python operations/releases/static_domain_standard.py validate
python operations/releases/static_domain_standard.py plan --domain agent-runtime
```

The domain name deterministically derives the protected environment, OIDC
audience and Kubernetes group. Repository and workflow remain explicit because
organization ownership is a security boundary. A plan never grants generic
cluster administration and never places credentials, kubeconfig, decrypted
Secrets or ordinary business state in Git.

Force takeover is a one-operation exception. The allowlist is populated only
from conflicts observed by the future executor identity during server-side
dry-run. Normal replay omits force and must succeed concurrently on isolated
self-hosted R640 jobs. The old Flux consumer is suspended, not deleted, until
takeover, replay, drift recovery, rollback and the risk-specific stability
window have passed.

Controller deletion is deliberately not a domain phase shortcut. It requires
zero real and source consumers, zero old-controller writes, safe orphaning of
finalizers, and healthy compatibility projections. These conditions are
machine-readable in every generated plan.

Trade-offs: the standard adds an approval and evidence step per release, but it
prevents cross-domain identities and whole-object force ownership. As the
system grows, revisit runner capacity and split critical infra/runtime stages;
do not relax identity or controller-removal gates to gain throughput.
