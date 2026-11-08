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

## GitOps reconcile records

Routine evidence and actionable work use different GitHub objects:

| Outcome | Record |
| --- | --- |
| Healthy reconcile | Append a deduplicated entry to the monthly `GitOps Reconcile Journal` page in the `service-backend` Wiki repository. |
| Failed, degraded or unknown reconcile | Create or update one source-specific Tracking Issue in `service-backend`, then add that Issue to `RE8CH GitOps Maintenance`. |
| Recovered reconcile | Append the healthy Wiki entry and close the open source-specific Tracking Issue. |

`gitops-reconcile-recording.json` is the reviewable routing and source inventory.

The Project therefore contains Issue-backed incidents only. It does not contain
routine observations, successful receipts, summary draft items or product ideas.
The Issue is the work record; the Project item is only its operational portfolio
view. Agent code fixes continue through pull requests. Product ideas begin as
Draft Issues and become Issues only when formally accepted for development.

The Maintenance App uses two short-lived installation tokens. A source token has
Actions read access only. The central recording token is restricted to
`re8ch/service-backend` with Contents and Issues write plus organization Projects
write. Wiki Git authentication uses a credential helper so the token is not put
in a remote URL or command argument.

## Runtime health evidence

The existing observation workflow polls the six domain workflows every 15 minutes.
It reads completed main-branch runs and their release artifacts, including scheduled
runs without a webhook delivery. A successful Action with degraded release results
is Degraded; absent or malformed readback is Unknown. Failed Actions remain Failed,
even if one artifact is healthy. Rollback is not target-release recovery. This uses
the existing Maintenance App and Projects without another controller or event relay.

The Admin API credential kubeconfig is runtime credential material. Its in-cluster
server must use `https://kubernetes.default.svc:443`; provisioners must not pin it
to a node that can leave the control plane. Credentials and CA remain in the
existing Secret; no credential values belong in Git or operation evidence.
