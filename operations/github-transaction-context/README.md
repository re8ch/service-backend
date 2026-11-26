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
