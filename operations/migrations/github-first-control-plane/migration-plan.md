# Migration plan and acceptance gates

Every phase is independently reversible. GitLab remains authoritative until the
Phase 3 cutover gate passes; dual-write is forbidden. During coexistence, GitLab
is the write authority and GitHub is a read-only mirror/projection.

## Wave 0 — discovery and decisions

Inventory repositories, teams, direct grants, deploy keys, variables, runners,
webhooks, protected branches, environments, releases, packages, submodules,
Git LFS, Issues/MRs, wiki, CI schedules and API consumers. Freeze creation of new
GitLab-only integrations.

Enterprise Owners have approved the three-domain topology in
`organization-topology.yaml`. Remaining Wave 0 decisions are:

- exact repository set and public/internal/private visibility;
- whether Issues/MRs and contribution attribution require a supported importer;
- data residency, audit retention and break-glass custodians.

Birfucate is currently embedded under
`platform/plugins/headlamp-birfucate-browser`; it must first be extracted with
history into a standalone repository. The aggregate `service-backend` and
`cloud-functions` authorities remain in `re8ch` through the provider cutover so
organizational moves do not also change Flux ownership.

**Gate:** signed inventory, dependency graph, data classification, owner for every
repo, and tested backup. **Rollback:** none required; no authority changed.

## Wave 1 — organization landing zone

Provision organization, SSO/SCIM teams, base permission `none`, custom roles,
custom properties, `.github` policy repository, rulesets, App allowlist, audit
stream and repository template through API/Terraform-compatible declarations.
Register separate intake, worker and deployment Apps. Build webhook gateway,
event contract validation, dedupe, queue and dead-letter behavior.

Create reusable CI/deploy workflows and establish OIDC trust, initially with no
production role. Import one non-runtime pilot repository, validate commit/tag/LFS
fidelity and exercise restore to a neutral Git server.

**Gate:** policy conformance, webhook replay safety, audit export, SSO recovery,
token-scope tests and mirror restore pass. **Rollback:** remove pilot App installs
and archive the GitHub pilot; GitLab remains writable.

## Wave 2 — compatibility and shadow operation

Mirror selected repositories GitLab → GitHub. Run GitHub Actions as non-blocking
shadow CI and compare artifacts/results with GitLab. Project events are generated
from GitLab work but GitHub Issues remain read-only projections. Update hard-coded
runbook URLs through a link map; inventory every `gitlab.com` and `glab` consumer.

Deploy the worker in observe-only mode: it may label/comment/check, but cannot
push or obtain Kubernetes credentials. Test alert storms, duplicate deliveries,
GitHub outage, expired leases and compromised-App revocation.

**Gate:** 14 days of CI parity, zero unexplained mirror drift, event loss SLO met,
and two successful disaster-recovery drills. **Rollback:** stop shadow workflows
and projections; continue GitLab unchanged.

## Wave 3 — control-plane cutover

Announce a change freeze. Stop GitLab writers and final-sync refs, tags, LFS,
releases and collaboration metadata. Verify object counts and commit IDs. Change
`service-backend` origin/index/catalog metadata, submodule URLs, CI badges and
runbook links in one reviewed GitHub PR. Configure Flux with the GitHub source
and credential while reconciliation is suspended; verify signed `main`, render
and diff, then resume a canary Kustomization before the root.

GitHub Issues/PRs/Projects become authoritative only after the timestamped
cutover record merges. Enable worker Contents write and production OIDC only
after rulesets and environment gates are proven.

**Gate:** signed GitHub HEAD, required checks, Flux source Ready, no desired-state
diff beyond the approved PR, canary healthy, audit/event paths healthy. **Rollback:**
suspend reconciliation, restore the previous Flux GitRepository URL/secret and
GitLab write authority, replay no GitHub-only changes; retain the cutover record.

## Wave 4 — repository waves

Move low-risk tooling, then controllers, then network/storage/security repos.
One repository per change window; update submodule gitlink URLs without changing
locked SHAs. For each repository verify clone, signed tags, releases, packages,
Actions, webhooks, team access, Issues/PR links and downstream consumers.

**Gate per repo:** owner sign-off, 7-day stable operation and tested rollback.
Network authority changes remain subject to
`operations/network/manifests/network/public-mesh/inventory.json`; this migration
does not alter restricted-node-6dc3f140f9 routes, addresses, Gateway or DNAT state.

## Wave 5 — GitLab retirement

Disable pipelines, runners, tokens, agents and webhooks only after dependency
telemetry shows no use for 30 days. Make projects read-only, retain redirects or
a compatibility mirror for the approved retention period, export audit and issue
mapping, then revoke credentials. Do not delete GitLab projects in this program.

**Exit criteria:** GitHub is the sole write authority; Flux and all automation
consume GitHub; no long-lived worker credential exists; alerts/feedback/agent and
deployment failures reliably become deduplicated GitHub events; quarterly Git
and collaboration export restores pass.

## Program scorecard

Track: migrated/total repos, unmapped identities, direct grants, policy drift,
mirror drift, webhook success/latency/duplicates/DLQ, Issue dedupe ratio, claim
lease failures, PR lead time, deployment success/rollback time, static credential
count, GitLab API traffic and restore RTO/RPO.
