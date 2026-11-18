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
