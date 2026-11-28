# Delivery context

> Generated Markdown view. The historical filename is retained for link
> compatibility. Runtime delivery no longer depends on Flux status.

| Domain | Desired-state repository | Current writer |
| --- | --- | --- |
| `agent-runtime` | `8iops/agent-gitops` | bounded Cloud SSA executor |
| `identity-access` | `re8ch/identity-gitops` | bounded Cloud SSA executor |
| `networking` | `pillars-infra/network-gitops` | bounded Cloud SSA executor |
| `observability-metering` | `siren-observer/observability-gitops` | bounded Cloud SSA executor |

The former `flux-*-system` controller namespaces and cutover locks are
historical evidence only. Current completion requires the accepted artifact,
cluster-specific SSA receipt, read-back evidence and independent runtime health.
