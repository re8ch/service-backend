# Enterprise GitHub security and admission policy

This policy separates universal safety boundaries from repository-specific
delivery requirements. It does not require every repository to publish the
same checks, use the same language or adopt identical merge presentation.

## Effective-state authority

- Read effective rules for the repository's default branch. The absence of
  classic branch protection is not a gap when Rulesets supply the controls.
- Record inaccessible and unavailable products separately from disabled ones.
- Verify required check names against successful recent check runs before
  activation. A nonexistent check is a repository outage, not protection.
- Treat a merge, deployment or security-product toggle as incomplete until the
  effective state is read back.

## Repository tiers

| Tier | Repository class | Required baseline |
| --- | --- | --- |
| 0 | Archived, mirror or preserved history | Read-only intent, deletion protection, Actions disabled unless a retained workflow is documented |
| 1 | Documentation, assets, profile and static content | Pull request, deletion and force-push prevention, signed commits, lightweight checks that actually exist |
| 2 | Product source, application and shared library | Tier 1 plus ownership review, dependency review, build/test checks, secret scanning and push protection |
| 3 | GitOps authority, security policy, identity, release and deployment control plane | Tier 2 plus linear history, strict checks, narrowly scoped App automation, protected Environments and runtime evidence |

Empty repositories receive a non-blocking template until their first delivery
path and checks exist. Forks inherit an explicit upstream or local policy rather
than being silently treated as product source.

## Ruleset layering

1. Enterprise Rulesets own universal invariants: repository deletion safety,
   default-branch deletion and non-fast-forward prevention, and forbidden broad
   bypass patterns.
2. Organization Rulesets select repository tiers through explicit include and
   exclude sets. They do not assume one check exists everywhere.
3. Repository Rulesets add real required checks and bounded delivery-specific
   constraints.
4. Personal users and broad roles are not standing bypass actors. A GitHub App
   may receive the narrowest bypass mode needed by its reviewed operation.
5. Every exception records owner, reason, expiry, affected repositories and the
   replacement or removal plan.

New or expanded Rulesets start in `evaluate`. Rule Insights and representative
human, Dependabot, release-App and emergency probes must show the expected
result before a canary subset becomes `active`. Broad activation follows only
after canaries and includes a reviewed rollback declaration.

## Security products

- Enable secret scanning and push protection on public repositories first.
- Estimate licensed Secret Protection cost before enabling private/internal
  repositories at scale. Tier 3 repositories are the first paid candidates.
- Prefer delegated push-protection bypass review to permanent exemption. Never
  exempt ordinary users.
- Enable Dependabot alerts broadly. Enable security-update pull requests only
  after grouping, concurrency and transaction-context ownership are defined.
- Enable Code Security/CodeQL by language and risk tier after its runner and
  licensing impact is known. Do not dispatch duplicate scanners for the same
  revision.
- AI secret detection is a separately costed preview capability and is not part
  of the baseline.

## Actions and deployments

- Default workflow tokens remain read-only and cannot approve pull requests.
- Move from `all` Actions to a reviewed allowlist, then require immutable SHA
  pinning only after existing workflows are clean. Preserve local reusable
  workflows and GitHub-owned actions needed for bootstrap.
- GitHub-hosted runners are the default. Self-hosted runners require an explicit
  host-bound capability and must not execute untrusted pull-request code with
  persistent credentials.
- Production and Tier 3 release Environments require an intended ref policy,
  least-privilege secrets, and a separately reviewed admin-bypass decision.
  An Environment name or secret alone is not deployment protection.

## Maintenance cadence

- Event-driven: audit every repository creation, visibility change, default
  branch change, Ruleset edit, bypass, Environment edit and security toggle.
- Daily: report only new critical drift or a lost Tier 3 protection.
- Weekly: reconcile the full inventory and exception expiries.
- Quarterly: exercise canary PRs, App automation, emergency rollback and
  Environment delivery; review licensed-product coverage and cost.

Routine developers should not inspect this inventory. Creation automation
selects the repository tier, installs the non-blocking template and provides one
corrective action when a required control cannot be satisfied.
