# Project context template

Use one Project for a product, service or governance domain. Repository-specific
rules may extend this template while preserving traceability and readable history.

## Project description

```markdown
## Outcome
<What durable capability or governance result does this Project own?>

## Scope
- Repositories: <owner/repository>
- Runtime surfaces: <services, clusters or public endpoints>
- Evidence: <Checks, Deployments, observability links or Wiki space>

## Context contract
Every PR closes an Issue. Every Issue belongs to this Project or a documented
parent Project. Parent Issues and Milestones express phases. Project fields
record status, type, lifecycle and dates.
```

## Hierarchy

```text
Project
├── parent Feature: outcome or historical phase
│   ├── Feature/Bug/Task: one reviewable concern
│   └── Feature/Bug/Task: one reviewable concern
└── parent Feature: next outcome or phase
```

Historical reconstruction groups PRs by meaningful capability or phase. Each PR
appears in one historical Feature. An archive Issue is reserved for evidence
whose meaning remains unresolved.

## Fields

| Field | Purpose |
| --- | --- |
| Status | Todo, In Progress, Done |
| Type | Feature, Bug, Task |
| Lifecycle | Backlog, Ready, In Progress, Blocked, Done, Cancelled, Historical |
| Work Type | Feature, Bug, Task, Governance, Dependency, Documentation, Migration, Historical |
| Context Status | Draft, Ready, Complete, Historical |
| Priority / Risk | Local prioritization and operational exposure |
| Start Date | First meaningful work or historical PR date |
| Target Date | Planned completion when applicable |
| Completed Date | Verified completion or final historical date |

## Views

1. `List`: table with Title, Status, Repository, Type, Parent issue,
   Sub-issues progress, Lifecycle, Work Type, Context Status and dates.
2. `Status`: board grouped by Status with the same context fields visible.
3. `Timeline`: roadmap using Start Date and Completed Date; use Target Date for
   unfinished work.

These three views are required navigation surfaces. Automation may repair their
field visibility, but must not replace user-defined filters, slices or additional
views. A missing required view is drift, not a reason to reset the Project.

## Draft and historical semantics

- Start an unaccepted proposal in Discussion. Add a native Project draft only
  when it needs scheduling or ownership before an Issue exists.
- `Context Status: Draft` means a live proposal awaiting acceptance. Once the
  proposal is accepted, convert it to an Issue and set `Context Status: Ready`.
- Imported document paths, completed migration notes and other reconstruction
  evidence use `Lifecycle: Historical` and `Context Status: Historical`. They
  must not appear as active Draft work even if GitHub still reports the native
  item type as `DraftIssue` pending a later archive migration.
- `Lifecycle: Done` is for completed tracked work; `Historical` is for evidence
  reconstructed after the fact. Neither state is active backlog.

## Labels and milestones

Project fields are authoritative for lifecycle and scheduling. Repository labels
are searchable projections, not a second state machine:

- `project:<domain>` identifies the owning Project.
- `work:<type>` projects Work Type for repository search and automation.
- `phase:<name>` identifies an accepted cross-repository phase.
- `planning:unphased` identifies accepted work that still needs a parent phase
  or Milestone; it is a visible intake queue, not an error developers must fix
  before opening a PR.

Use Milestones for bounded delivery phases. Children inherit the phase Milestone
from their parent outcome. Do not invent a deadline for unscheduled backlog;
apply `planning:unphased` until triage assigns a phase. Closed reconstructed work
may use a dated historical-reconstruction Milestone so it remains queryable
without contaminating current delivery plans.

## Issue template

```markdown
## Outcome
<Observable result>

## Context
- Parent Issue: #
- Project: <link>
- Milestone: <name>
- Branch/worktree: <name>

## Acceptance
- [ ] Static contract or code verification
- [ ] Runtime verification where applicable
- [ ] Evidence linked below

## Evidence
<Checks, deployment status, sanitized Kubernetes output, observability or Wiki>
```

## Delivery rules

- One Issue, concern, branch and worktree form one review unit.
- Commits describe coherent state transitions and remain signed.
- The PR closes the Issue and links immutable build and runtime evidence.
- Each CI class runs once for an unchanged revision.
- Source comments explain invariants; Issues/Wiki carry narratives and output.
- Project status changes after the corresponding evidence is verified.
