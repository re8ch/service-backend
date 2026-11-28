# Retired native Flux self-service proposal

> This proposal was superseded on 2026-10-27 and is retained only as a stable
> historical link. Local and autonomous Flux writers are permanently retired.
> Do not publish or execute the former self-service workflow, restore its
> controller, or move Cloud SSA units back to Flux.

Current self-service uses reviewed GitHub desired state, protected GitHub
Environments, cluster-specific OIDC and the bounded cloud SSA executor. Runtime
observation is read-only and separate from execution.

Use [Cloud SSA domain delivery](DOMAIN-RECONCILIATION.md) for the current model.
The former Flux inventory, `flux tree` procedures, dynamic skill and migration
steps remain available in Git history and the restricted operations Project when
historical investigation is required.
