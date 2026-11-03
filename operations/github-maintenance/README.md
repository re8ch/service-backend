# GitHub-native maintenance

The `re8ch-gitops-maintenance` GitHub App owns issue, Project, reporting, and
protected merge execution. It does not decide what should merge. For this
single-maintainer organization, a manual workflow dispatch by `Woomou` is the
human authorization; the App is the separately attributable minimal executor.

To authorize one exact revision after reviewing it:

```console
gh workflow run github-bot-merge.yml --ref main \
  -f pull_request=1234 \
  -f head_sha=<exact-40-character-head-sha>
```

The workflow runs only from `main`, rejects other actors, forks, drafts,
unverified commits, untrusted authors, stale head SHAs, and outstanding change
requests. The final merge request includes the authorized SHA. GitHub's active
`main` ruleset remains authoritative for required checks, strict up-to-date
status, resolved conversations, signatures, linear history, and allowed merge
methods. The App is not a ruleset bypass actor.

The `gitops-merge` Environment is restricted to `main`. Its job uses the
repository's Maintenance App ID and private-key secret to request a short-lived
installation token with only `contents:write` and `pull_requests:write`. The
default workflow token remains read-only.

## Repository-specific GitOps observation Projects

`gitops-observation-projects.json` declares one organization-level Project for
each GitOps repository. Each Project is linked to its repository so it appears
on that repository's Projects page. These Projects hold bounded observation
results, reconcile actions, revisions, workflow run IDs and links to redacted
logs. Raw telemetry and credentials remain outside GitHub.

`RE8CH GitOps Maintenance` remains the cross-repository summary and escalation
view. Repository-specific observations must not be added to that summary
Project.

Preview and apply the reviewed declaration with:

```console
python operations/github-maintenance/reconcile_observation_projects.py
python operations/github-maintenance/reconcile_observation_projects.py --apply
```

The apply command uses GitHub's ProjectV2 GraphQL API, is idempotent for existing
projects and fields, and reads back Projects and fields before creating missing
state.
