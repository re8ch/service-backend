# Infrastructure collaboration domain

> **Historical migration record.** This document preserves a point-in-time
> transition and is not current operating guidance. Local and autonomous Flux
> writers are retired; GitHub Actions and the bounded cloud SSA executor are the
> only platform delivery writer. Do not execute Flux bootstrap, suspend, resume
> or recovery steps from this document. Use
> [`operations/releases/DOMAIN-RECONCILIATION.md`](https://github.com/re8ch/service-backend/blob/main/operations/releases/DOMAIN-RECONCILIATION.md)
> for the current authority and delivery model.


This organization uses GitHub as its coordination and event bus, not as its
runtime manager.

- Git and pull requests carry desired-state proposals and transitions.
- Kubernetes/K3s executes runtime state.
- At that migration stage, Flux reconciled approved Git state.
- Headlamp provides lightweight runtime inspection.
- Prometheus, logs and traces remain in observability backends.
- GitHub Issues retain actionable events, decisions, evidence links and audit
  history, never bulk raw telemetry.

