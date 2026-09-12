# Infrastructure collaboration domain

This organization uses GitHub as its coordination and event bus, not as its
runtime manager.

- Git and pull requests carry desired-state proposals and transitions.
- Kubernetes/K3s executes runtime state.
- Flux reconciles approved Git state.
- Headlamp provides lightweight runtime inspection.
- Prometheus, logs and traces remain in observability backends.
- GitHub Issues retain actionable events, decisions, evidence links and audit
  history, never bulk raw telemetry.

