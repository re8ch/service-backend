# Retired Flux self-service workflow

This reviewed workflow is withdrawn. The historical filename is retained so old
Issue and dynamic-skill references fail safely into current guidance.

Do not inspect, annotate, reconcile, suspend or resume former Flux sources or
Kustomizations. Do not publish the earlier workflow body as a dynamic skill.
Local and autonomous Flux writers are retired.

Current self-service must:

1. resolve the active organization and published capability;
2. create reviewed GitHub desired state in the owning repository;
3. use the protected cluster-specific Cloud SSA operation;
4. read back accepted/applied state and independent runtime health;
5. keep credentials in approved secret systems.

Use [`../DOMAIN-RECONCILIATION.md`](../DOMAIN-RECONCILIATION.md) for the current
delivery model and the Re8ch plugin's current capability discovery for supported
tenant operations.
