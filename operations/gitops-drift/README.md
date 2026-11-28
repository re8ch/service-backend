# Retired Flux drift audit

This tool is retained for historical report interpretation and compatibility
tests. Its `gitops` mode reads Flux Kustomization status and therefore cannot
establish current delivery or drift after controller retirement.

Current drift evaluation compares the reviewed Cloud SSA contract and accepted
inventory with server-side read-back under the exact target and field manager.
Tenant boundary audits remain read-only but must not be combined with historical
Flux status to claim convergence.

Do not schedule this tool as a current observer or use its previous reports as
runtime health. Use the bounded Cloud SSA observer and record `Unknown` when
fresh read-back is unavailable.
