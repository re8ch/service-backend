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

Regenerate the repository set and validate it with:

```bash
python operations/github-transaction-context/inventory.py --write
python operations/github-transaction-context/inventory.py
```

The generator merges the developer-context catalog with the runtime GitOps
authority list. Enforcement remains `blocked` where a repository cannot yet
obtain the bounded private-Project read identity; formatting alone never makes
that check enforceable.
