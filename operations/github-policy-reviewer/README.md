# GitHub policy reviewer

This workflow runs from `re8ch/service-backend`'s protected `main` branch every
five minutes and can be started manually. It mints a separate installation token
for each of `public-edge`, `advanced-fabrics`, `among-clusters`, and
`bifurcate-systems`. The App has
read-only repository, actions and status permissions plus checks and PR-review
write permissions; it cannot push commits or administer repositories.

The reviewer approves only open, non-draft PRs against the repository default
branch that originate in the same repository, are authored by `Woomou`, contain
only verified commits attributed to `Woomou`, have at least one successful check
and no incomplete or failed checks/statuses, and have no outstanding change
request. It rechecks the PR head immediately before publishing an App-owned
`re8ch-policy-reviewer` check run. Eligible PRs receive a successful check and
an informational App approval. Ineligible recent PRs receive a failing or
pending check. The workflow never checks out or executes PR content.
PRs opened before the rollout cutoff are deliberately excluded so the reviewer
does not retroactively approve an old backlog.

The private key lives only in the private repository Actions Secret
`POLICY_REVIEWER_PRIVATE_KEY`. `POLICY_REVIEWER_ENABLED=true` enables the job.
Unset `POLICY_REVIEWER_DRY_RUN` or set it to `true` to log eligibility without
submitting checks or reviews; set it to `false` after a successful dry run.
Disable by setting `POLICY_REVIEWER_ENABLED=false` or removing the secret.

GitHub App installation scope and branch rules are managed in each organization.
GitHub does not count App approvals toward required human reviews. These
organizations therefore require the App-owned `re8ch-policy-reviewer` check
(integration ID `4973967`) and zero human approvals, while retaining the PR,
signature, non-fast-forward and existing CI requirements. An external
contributor's PR cannot pass this check; a maintainer must bring its change into
a new trusted, signed PR. The App does not auto-merge or publish a release.
Repository auto-merge may be enabled separately once the check passes.
