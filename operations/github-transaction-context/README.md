# GitHub transaction context

`transaction-context` is the merge gate for the repository's rich-context
delivery contract. It validates GitHub relationships rather than parsing
unstructured references from pull-request text.

For every non-exempt pull request, the check requires:

- at least one Issue connected through a closing keyword;
- the Issue number in the branch name;
- a native Issue Type;
- an explicit parent Issue or `Parent Issue: none` declaration;
- membership in an approved organization Project; and
- Status, Lifecycle, Work Type, Context Status, Start Date, plus a Target or
  Completed Date on at least one approved Project item.

The workflow runs on `pull_request_target` so organization Project credentials
remain available. It always checks out the policy from `main`; pull-request code
is never executed with the maintenance App token. Dependabot is exempt only
when both the authenticated author and standard branch prefix match the reviewed
entry in `config.json`. Other automation must carry normal transaction context.

Change the policy through a signed Issue branch and pull request. Add the
`transaction-context` job to the main Ruleset only after a successful check has
been observed on the default-branch workflow.

## Repository rollout inventory

`repository-inventory.json` is the catalog-derived rollout ledger for the
minimum relationship contract. It records capabilities independently so an
existing repository-specific contract can satisfy the requirement without
adopting this repository's presentation. A `missing` value is an explicit audit
queue item, not evidence that the repository lacks useful local conventions.
This is an automation/maintainer ledger, not a checklist for ordinary
developers.

The intended developer path has one entry point: create or select an Issue and
state the desired outcome. Automation should enroll it in the default Project,
apply deterministic field defaults, derive the branch and PR identity, maintain
the closing relationship and link checks and revisions. Humans decide only
ambiguous ownership, acceptance and the meaning of runtime evidence. A failed
check should offer one corrective action; it must not require developers to
inspect this inventory or manually reconcile multiple GitHub surfaces.

For service-backend, `transaction-context-intake.yml` implements that path on
new Issues. It runs trusted `main` code on a GitHub-hosted worker, uses the
bounded maintenance App, applies reviewable defaults, and posts one branch/PR
instruction. The two-week target is a planning default, not an inferred
commitment; changing it is the only date action needed when the default is
wrong. Reconciliation is idempotent and never executes Issue-supplied code.

The five GitOps authorities use the central
`gitops-transaction-context-intake.yml` reconciliation matrix. This is the
bounded fallback for GitHub's missing cross-repository Issue-event bridge: the
Maintenance App key remains only in service-backend, each matrix job mints a
short-lived token for one repository and organization Project, and the scan is
limited to the 30 most recently updated open Issues. Items carrying the intake
marker are skipped, and existing Project field values are preserved. The
schedule adds at most a 15-minute delay without adding any developer step.

## Historical automation factors

`automation-factor-policy.json` defines the only automation identities that may
be aggregated during open-PR backfill. Classification requires the exact App
identity and its reviewed branch shape; the domain promoter additionally needs
the reviewed title contract. Similar names and unknown Apps fail closed to
manual review.

Dependabot factors are bounded by repository and package ecosystem. Domain OCI
promotion factors are bounded by repository and promotion stream. This avoids
creating one hand-maintained Issue per bot PR while retaining one primary Issue
that owns lifecycle for each automation stream. Cross-organization and
cross-Project factors remain additive secondary relationships.

The classifier consumes audit-ledger JSON without changing GitHub state:

```bash
python operations/github-transaction-context/classify_automation_factor.py \
  --input open-pr-ledger.json
```

An `aggregate` decision permits a later reviewed migration to reuse the factor
key; it never permits closing a PR. `review` means no automatic factor may be
assigned.

Regenerate the repository set and validate it with:

```bash
python operations/github-transaction-context/inventory.py --write
python operations/github-transaction-context/inventory.py
```

The generator merges the developer-context catalog with the runtime GitOps
authority list. Enforcement remains `blocked` where a repository cannot yet
obtain the bounded private-Project read identity; formatting alone never makes
that check enforceable.

## Pull-request factors

`factor-model.json` defines a factor as an Issue that explains why a pull
request exists. Every human PR has exactly one primary factor with the native
closing relationship; it owns lifecycle, Project state and acceptance. A PR
may also carry secondary `contributes_to`, `depends_on`, `blocks` or `observes`
factors. Those factors may cross repositories, organizations and Projects and
use the canonical `owner/repository#number` identity.

`factor_audit.py` validates normalized records produced by the PR audit ledger.
It does not force all factors into one Project and does not treat a secondary
cross-organization reference as a substitute for primary lifecycle ownership.
Repository-local governance can add domain requirements without changing this
minimum relationship contract.
