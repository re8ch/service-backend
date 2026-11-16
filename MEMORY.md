# Local reconciliation memory

- `AGENTS.md` is authoritative.
- Read `docs/GITHUB_OPS_RECONCILE_DEBUGGING.md` before debugging GitHub Actions
  access to Kubernetes.
- Separate TLS/transport, OIDC identity, RBAC, accepted state, signature/pull,
  executor dry-run/apply, and runtime health. A later step can fail after an
  earlier success message.
- Use the Re8ch Admin Governance service for workflow dispatch. Do not use local
  GitHub credentials or local Flux as a controller.
- R640 is a bounded fallback for applying an exact reviewed bootstrap object;
  dry-run first, avoid wildcard permission and broad force-conflicts, then read
  back authorization and runtime state.

