# GitHub Enterprise security governance

This directory owns read-only inventory and reviewed policy contracts for
GitHub repository admission and security controls. Kubernetes and runtime
security remain separate authorities.

- `POLICY.md` defines the tiered target and rollout safety.
- `BASELINE-2026-11-27.json` records the aggregate initial readback without
  publishing the private repository-level inventory.
- `audit.py` produces the detailed local evidence used for reconciliation.

Run the live audit with a token that can read every Enterprise organization and
repository administration surface:

```bash
GH_TOKEN="$(gh auth token)" python operations/github-security-governance/audit.py \
  --enterprise re8ch --output /tmp/re8ch-enterprise-security.json
```

The report records HTTP status separately from values. A `404` may mean that a
classic protection rule is absent, that a product is unavailable, or that the
identity cannot see the surface; policy evaluation must not collapse those
cases without corroborating repository and effective-ruleset evidence.

## Maintenance model

1. Enterprise policy defines non-negotiable boundaries and forbidden bypass.
2. Organization Rulesets provide risk-tier defaults.
3. Repository Rulesets add required checks that actually exist in that
   repository.
4. Time-bounded exceptions name an owner, reason, expiry and replacement plan.
5. New Rulesets begin in `evaluate`, advance through representative canaries,
   and become `active` only after pull-request and automation probes pass.
6. Drift audits are read-only and quiet on success. Mutations require a tracked
   Issue, reviewed declaration, effective-state readback and rollback evidence.

Do not require a check globally until every targeted repository publishes that
check. Do not grant bypass to personal users or broad teams. GitHub Apps receive
only the narrow bypass or merge authority their reviewed operation requires.
