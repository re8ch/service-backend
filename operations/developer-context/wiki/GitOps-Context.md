# Delivery context

| Domain | Desired-state repository | Current writer |
| --- | --- | --- |
| `agent-runtime` | ssh://git@ssh.github.com:443/8iops/agent-gitops.git | bounded Cloud SSA executor |
| `identity-access` | ssh://git@ssh.github.com:443/re8ch/identity-gitops.git | bounded Cloud SSA executor |
| `networking` | https://github.com/pillars-infra/network-gitops | bounded Cloud SSA executor |
| `observability-metering` | ssh://git@ssh.github.com:443/siren-observer/observability-gitops.git | bounded Cloud SSA executor |

Completion requires the accepted artifact, cluster-specific SSA receipt,
read-back evidence and independent runtime health.
