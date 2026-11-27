# Actions and deployment Environment governance

This contract stages immutable workflow provenance and deployment protection
without making ordinary development depend on manual policy inspection.

## Actions rollout

1. Inventory every `uses:` reference on the default branch. Local actions and
   local reusable workflows remain permitted; external actions must resolve to
   a full commit SHA before SHA enforcement.
2. Preserve default workflow permissions as read-only with pull-request review
   approval disabled.
3. Move a repository from `all` actions to an allowlist only after its GitHub-
   owned, organization-owned, third-party, Docker and reusable-workflow sources
   are known. Bootstrap and recovery workflows must remain representable.
4. Enable immutable SHA enforcement first on low-risk docs/assets canaries that
   already contain no mutable external reference. Run an actual workflow and
   read back the effective repository setting before expanding the canary.
5. A mutable reference finding creates a remediation queue item; it does not
   silently rewrite workflow source or block unrelated repositories.

Rollback restores the exact prior `enabled`, `allowed_actions` and
`sha_pinning_required` values. A rollback never broadens workflow-token write
permissions or enables pull-request approval.

## Runner placement

GitHub-hosted runners are the default for CI, builds, tests, scans and policy
checks. A self-hosted job requires a reviewable host-bound reason such as direct
access to a named private runtime, a device, a retained signing boundary or an
explicit platform capability unavailable on GitHub-hosted infrastructure.

Self-hosted runners must not execute untrusted pull-request code with persistent
credentials. Runner labels are scheduling intent, not isolation evidence.
Exceptions record the workflow, job, capability, credential boundary, trigger,
expiry and GitHub-hosted replacement condition.

The historical `check_no_github_hosted_runners.py` gate still prefers
self-hosted placement for many jobs and is therefore migration debt. It must be
replaced through a separate Issue with a positive hosted-default policy; this
baseline does not weaken or bypass the existing gate.

## Environment classes

| Class | Intended controls |
| --- | --- |
| Pages or preview | Explicit publication branch; no production secret; admin bypass may remain when documented |
| Production application | Protected/default or explicit release refs; least-privilege secrets; reviewer decision based on blast radius |
| Tier 3 executor | Exact reviewed ref policy, required reviewer or narrow App gate, admin bypass disabled by default, immutable artifact/revision evidence |
| Tier 3 reconciler | Exact main/release ref policy, no arbitrary dispatch input, admin bypass disabled by default, runtime readback required |
| Recovery | Manual trigger, incident/approval evidence, protected ref, bounded credentials, explicit post-recovery revocation |

An Environment with neither protection rules nor a deployment branch policy is
unprotected regardless of its name. `can_admins_bypass` is an explicit risk
decision, not a convenience default. Environment changes use one canary at a
time because a wrong ref rule can stop production delivery.

## Maintenance

- Repository creation installs read-only workflow permissions and an Actions
  inventory marker; allowlist and SHA enforcement remain non-blocking until the
  source is ready.
- Pull requests changing workflows run the reference audit and runner-placement
  policy once per revision.
- Daily drift reports only lost read-only permissions, newly mutable references,
  or lost Tier 3 Environment protection.
- Weekly reconciliation checks allowlists, SHA enforcement, Environment ref
  policy, reviewer identity and admin-bypass exceptions.
- Quarterly exercises validate one hosted build, one Tier 3 deployment, one App
  automation path and one rollback without exposing Environment secrets.
