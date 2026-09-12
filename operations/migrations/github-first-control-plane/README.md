# RE8CH GitHub-first collaboration control plane

Status: execution started, 2026-09-12. The approved organization topology is
declared in `organization-topology.yaml`.

## Decision

RE8CH will use GitHub Enterprise as the collaboration authority and Git as the
durable state-transition log. Kubernetes remains the runtime authority. Metrics,
logs, traces, artifacts and secrets remain in their purpose-built systems; only
their actionable, redacted projections become GitHub events.

The operating grammar is:

| Concept | Canonical representation | Meaning |
|---|---|---|
| work/event | Issue | one addressable unit with owner, lifecycle and evidence links |
| proposal/review | Pull request | the only normal path to change protected desired state |
| state transition | signed commit | immutable transition in a reviewable history |
| reaction | Actions or verified webhook | deterministic response to an event |
| portfolio state | GitHub Projects | cross-repository view, never an independent source of truth |
| runtime state | K3s/Kubernetes | observed execution state reconciled from Git |
| telemetry | Prometheus/Loki/tracing stores | high-volume raw data, linked from Issues |

GitHub-first is not GitHub-only. Every authoritative declaration is a portable
file with a versioned JSON Schema; webhook deliveries are normalized into the
provider-neutral event envelope in `collaboration-contract.yaml`; Git mirrors,
release bundles and audit exports are continuously verified.

## Target topology

```text
GitHub Enterprise
├── re8ch                         product organization
│   ├── product source repos
│   └── product roadmaps / customer-facing work
├── advanced-fabrics              fabric ecosystem and conformance
├── among-clusters                multi-cluster, Birfucate and ConnecTool ecosystem
├── public-edge                   PublicEdge and Mihomos-Cluster ecosystem
└── re8ch                         product organization and aggregate GitOps authority

GitHub webhooks ──> event gateway ──> queue/dedup ──> policy router
                                             ├── GitHub Issue/Project mutation
                                             ├── AI worker lease/job
                                             └── observability correlation
GitHub Actions ──OIDC──> identity broker ──> scoped Kubernetes service account
GitHub main HEAD ──────> Flux source-controller ──> K3s reconciliation
```

The event gateway verifies webhook signatures, records delivery ID and payload
hash, redacts payloads, applies idempotency on `(provider, delivery_id)`, and
queues work before returning 2xx. It never runs cluster mutations in the HTTP
request. A dead-letter item opens or updates one operations Issue.

## Repository boundaries and layout

`service-backend` remains the aggregate desired-state repository initially. Do
not split Kubernetes ownership during the provider cutover. After parity is
proven, move independently releasable controllers to their own repositories;
consume immutable OCI artifacts or commit/digest references here.

Every infrastructure repository converges on:

```text
.github/
  CODEOWNERS
  ISSUE_TEMPLATE/{task,incident,alert,customer-feedback,agent-task}.yml
  pull_request_template.md
  workflows/                 thin callers only
docs/{adr,rfc,runbooks}/
schemas/                     portable machine contracts
deploy/                      GitOps declarations or immutable release pointers
src/                         implementation, when applicable
tests/
SECURITY.md
```

Each infrastructure organization's `.github` repository owns reusable workflows, issue forms,
label definitions, repository custom-property definitions and ruleset exports.
Each repository pins reusable workflows and third-party Actions by full commit
SHA. Repository custom properties must include `system`, `service-tier`,
`data-class`, `runtime`, `owner-team`, `gitops-role`, and `lifecycle`.

## Collaboration contracts

### Labels

Labels are namespaced and machine-managed from `collaboration-contract.yaml`.
Exactly one label from each required dimension is allowed:

- `type/*`: task, incident, alert, feedback, decision, change, agent-task
- `status/*`: triage, ready, claimed, blocked, review, done, cancelled
- `priority/*`: p0, p1, p2, p3
- `area/*`: control-plane, network, storage, observability, security, developer-experience
- `actor/*`: human, agent, automation, customer
- `risk/*`: low, medium, high, critical

State transitions are monotonic unless an explicit `reopened` event records the
reason. Closing is not deletion. Automation must not infer priority from prose.

### Issue schema

Every Issue contains visible YAML in a fenced `re8ch-event` block so it remains
portable and API-readable:

```yaml
schema: collaboration.re8ch.com/issue/v1
event_id: 01J...
source: {kind: alert, system: vmalert, external_id: "..."}
service: {repo: re8ch-infra/service-backend, component: flux}
severity: p1
environment: production
dedupe_key: sha256:...
occurred_at: 2026-09-12T00:00:00Z
evidence:
  - {kind: dashboard, url: "https://...", redaction: safe}
acceptance: ["Flux source Ready", "main HEAD signature verified"]
```

Required prose sections are Summary, Impact, Evidence, Acceptance criteria,
Rollback and Audit. Alerts update one Issue per dedupe key and append occurrence
counts; they do not create an Issue per sample. Customer feedback is scrubbed of
PII and linked to the system of record by opaque ID.

### Pull request and commit schema

PRs require `Change`, `Why`, `Risk`, `Validation`, `Rollout`, `Rollback`,
`Issue: closes #N`, and an `audit` block containing schema version, change ID,
actor class, affected systems and evidence links. High/critical risk changes
require platform and domain CODEOWNER approval plus a protected environment gate.

Commits use `type(scope): subject`, are signed, and carry trailers:

```text
Issue: re8ch-infra/service-backend#123
Change-Id: chg_01J...
Actor-Class: human|agent|automation
```

Merge commits or squash commits are accepted only through the merge queue. A
force push, branch deletion bypass, unsigned release tag or direct write to
`main` is denied by organization rulesets.

## Permission and identity model

| Principal | Access | Constraints |
|---|---|---|
| Enterprise owners | enterprise policy only | two break-glass humans; no routine repo work |
| Organization owners | org/app/team administration | separate daily account; changes exported and reviewed |
| `platform-admins` | maintain/admin selected infra repos | cannot bypass high-risk rules alone |
| domain teams | triage/write/maintain by repo | team grants only, no direct-user grants |
| auditors | read + audit export | no mutation |
| AI worker GitHub App | Issues/PRs/Checks write; Contents write | selected repos, one-hour installation token, no Administration |
| deploy workflow | OIDC federation | environment + reusable-workflow claims; no static kubeconfig |
| Flux | repository read/deploy key or App token | read-only, HEAD signature verification |

Enterprise Managed Users/SSO and SCIM groups should back human teams. Base
permission is `none`; outside collaborators are time-bounded and repository
scoped. Require 2FA where EMU is not used. Use custom repository roles rather
than `admin` for common maintenance duties.

The worker App receives only Metadata read, Issues write, Pull requests write,
Checks write and Contents write. Workflow-file mutation is a separate App and
approval path. Tokens are narrowed again to the leased repository and required
permissions. The worker never receives an organization PAT.

OIDC trust checks `iss`, `aud`, repository owner, environment and pinned reusable
workflow identity. The broker maps that claim to a short-lived, namespace-scoped
Kubernetes identity. Production mutation is allowed only to the deployment
workflow on protected `production`; ordinary AI jobs can test but cannot deploy.

## Automation workflows

1. **Intake:** Alertmanager/customer/support/agent gateway normalizes an event,
   deduplicates it, creates or updates an Issue, applies labels and adds it to the
   organization Project.
2. **Lease:** `/agent claim` or `status/ready` triggers a compare-and-swap lease
   recorded in an Issue comment and external job store. The App changes status to
   `claimed`; lease expiry returns it to `ready`.
3. **Change:** Worker creates `agent/<issue>-<slug>`, pushes signed commits, opens
   a draft PR, and posts check runs. It cannot self-approve or bypass rules.
4. **Review:** CODEOWNERS, policy checks, schema/render/unit/security tests and
   merge queue decide admission. Human approval is mandatory for high risk.
5. **Deploy:** A pinned reusable workflow obtains OIDC, builds/signs/SBOMs an
   artifact, and updates GitOps desired state by PR. Flux deploys only verified
   `main`; deployment status and evidence flow back to the PR/Issue.
6. **Observe:** A deployment anomaly reopens the originating Issue or creates a
   linked incident. Raw telemetry remains outside GitHub.

All handlers use delivery IDs and change IDs as idempotency keys, exponential
backoff with jitter, rate-limit headers, bounded retries and a dead-letter queue.
Webhook ingress is active/active; workers are horizontally scalable and stateless.

## Required organization controls

- Enterprise allowlist for Actions and GitHub Apps; SHA pinning and dependency review.
- Organization rulesets targeting custom properties, with signed commits,
  pull requests, CODEOWNERS, required checks, merge queue and no deletion/force push.
- Secret scanning, push protection, Dependabot and code scanning as applicable.
- Repository creation only through a template/provisioning API; settings drift is
  reconciled from declarations in `.github`.
- Audit-log streaming plus daily export of Issues, PRs, Projects, teams, rulesets,
  App installations and repository metadata to immutable storage.
- Git mirrors and release bundles tested by quarterly restore exercises.

GitHub Projects is a projection: automation derives `Status`, `Priority`,
`Service`, `Environment`, `Risk`, `Owner`, `Target date` and `Change ID` from
Issues/PRs. A Project field edit must write back to the Issue contract or be
reverted; otherwise two sources of truth emerge.

## Availability and lock-in boundaries

GitHub outage freezes collaboration mutations but must not stop the running
cluster. Flux retains the last applied state; emergency runtime intervention
uses a time-boxed, audited break-glass procedure and is immediately reconciled
back through Git after recovery. Provider APIs sit behind adapters; the canonical
event, policy, repository and audit models are provider-neutral files. Do not put
business logic only in Projects automations or opaque GitHub-hosted configuration.

Revisit repository splitting, event archive scale and self-hosted runners when
the infrastructure organization exceeds roughly 30 active repositories, webhook
traffic exceeds the gateway's tested envelope, or compliance requires stronger
workload isolation.

## Official capability references

- GitHub organization rulesets: <https://docs.github.com/en/organizations/managing-organization-settings/creating-rulesets-for-repositories-in-your-organization>
- GitHub App least privilege: <https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app>
- Installation tokens: <https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app>
- OIDC reusable workflows: <https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows>
- Deployment environments: <https://docs.github.com/en/actions/concepts/workflows-and-actions/deployment-environments>
