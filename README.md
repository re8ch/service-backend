# RE8CH Service Backend

[![Control plane CI](https://github.com/re8ch/service-backend/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/re8ch/service-backend/actions/workflows/ci.yml?query=branch%3Amain)
[![Cloud replay](https://github.com/re8ch/service-backend/actions/workflows/main-domain-reconcile.yml/badge.svg?branch=main)](https://github.com/re8ch/service-backend/actions/workflows/main-domain-reconcile.yml?query=branch%3Amain)
[![Security checks](https://github.com/re8ch/service-backend/actions/workflows/github-security-checks.yml/badge.svg?branch=main)](https://github.com/re8ch/service-backend/actions/workflows/github-security-checks.yml?query=branch%3Amain)

GitHub-first collaboration and desired state for the RE8CH platform. GitHub
stores reviewed intent and delivery evidence; Kubernetes owns runtime state;
GitHub Actions and the bounded cloud SSA executor apply accepted immutable
artifacts; Headlamp and observability systems expose live state.

The bounded cloud SSA executor is the permanently selected write path. All
other cluster delivery write paths are forbidden.

## Collaboration model

Every change follows one connected context chain:

```text
Project → parent Issue → Issue → branch/worktree → signed commits → pull request
        → checks → merge → runtime evidence → Project status
```

- A Project represents a product, service or governance domain.
- Parent Issues represent outcomes or historical phases.
- Issues define one reviewable change and provide the PR's closing context.
- A branch and worktree belong to one Issue and one concern.
- Commits explain the smallest coherent state transition.
- Pull requests link the Issue and reuse each CI class once per revision.
- Project fields record status, type, lifecycle and dates.
- Runtime observations, Kubernetes output and investigation narratives live in
  Issues, Discussions, Wiki or the observability backend.

Use [the Project context template](.github/PROJECT_CONTEXT_TEMPLATE.md) when
creating or reconstructing a Project. Its standard views are `List`, `Status`
and `Timeline`.

## Repository scope

- `catalogs/`: product catalogs and locked source revisions
- `clusters/re8ch/`: cluster desired-state composition
- `platform/`: shared platform applications and capabilities
- `inventory/`: non-secret infrastructure inventory
- `operations/`: release, Cloud SSA delivery, observation and host-bound operations
- `runbooks/`: operational procedures and historical investigations
- `schemas/`: declarative contracts
- `tests/`: policy and contract verification

Product source remains in its independent repository. The authority ledger is
[`operations/flux-federation/authority.yaml`](operations/flux-federation/authority.yaml),
and Cloud SSA delivery is described in
[`operations/releases/CLOUD-SSA-DELIVERY.md`](operations/releases/CLOUD-SSA-DELIVERY.md).

## Working here

Run `python operations/workspace-controller/workspace.py doctor`, then create a
task worktree from `github/main`. Follow
[`operations/workspace-controller/GITOPS-WORKTREE-POLICY.md`](operations/workspace-controller/GITOPS-WORKTREE-POLICY.md)
and the root [`AGENTS.md`](AGENTS.md).

Keep desired configuration declarative, code small and comments focused on
non-obvious invariants. Link durable operational context from Issues and Wiki.

Credential configuration in Git contains names, scopes and rotation requirements;
approved secret systems contain values.

## Entry points

- External project delivery: [`runbooks/external-project-deployment.md`](runbooks/external-project-deployment.md)
- Service public-path network investigation: [`runbooks/service-public-path-network-investigation.md`](runbooks/service-public-path-network-investigation.md)
- Cloud SSA delivery: [`operations/releases/CLOUD-SSA-DELIVERY.md`](operations/releases/CLOUD-SSA-DELIVERY.md)
- Static public sites: [`operations/public-sites/README.md`](operations/public-sites/README.md)
- Tenant self-service: [`platform/apps/tenant-mcp/README.md`](platform/apps/tenant-mcp/README.md)
- Restricted topology access: [`contracts/network-topology/README.md`](contracts/network-topology/README.md)
- Public evidence privacy: [`contracts/public-evidence-privacy/README.md`](contracts/public-evidence-privacy/README.md)
