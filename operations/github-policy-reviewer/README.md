# GitHub policy reviewer

This workflow runs from `re8ch/service-backend`'s protected `main` branch every
five minutes and can be started manually. It mints a separate installation token
for each of `public-edge`, `advanced-fabrics`, and `among-clusters`. The App has
read-only repository, checks, actions and status permissions plus PR-review
write permission; it cannot push commits or administer repositories.

The reviewer approves only open, non-draft PRs against the repository default
branch that originate in the same repository, are authored by `Woomou`, contain
only verified commits attributed to `Woomou`, have at least one successful check
and no incomplete or failed checks/statuses, and have no outstanding change
request. It rechecks the PR head immediately before approving. Other PRs remain
for manual handling. The workflow never checks out or executes PR content.
PRs opened before the rollout cutoff are deliberately excluded so the reviewer
does not retroactively approve an old backlog.

The private key lives only in the private repository Actions Secret
`POLICY_REVIEWER_PRIVATE_KEY`. `POLICY_REVIEWER_ENABLED=true` enables the job.
Unset `POLICY_REVIEWER_DRY_RUN` or set it to `true` to log eligibility without
submitting reviews; set it to `false` after a successful dry run to submit them.
Disable by setting `POLICY_REVIEWER_ENABLED=false` or removing the secret.

GitHub App installation scope and branch rules are managed in each organization.
`public-edge` currently requires a code-owner approval, which an App review
cannot satisfy; after the dry run succeeds, that one requirement must be turned
off while keeping one required review, last-push approval, signatures and CI.
This App does not auto-merge or publish a release. Repository auto-merge may be
enabled separately once approval and required checks pass.
