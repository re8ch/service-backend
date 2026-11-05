# GitHub-native developer context

`workspace/developer-context.yaml` declares how source repositories, GitOps
repositories, generated module memory, Codespaces, worktrees, Wiki, Projects
and Gists fit together. Git on `github/main` is the authority. Wiki, Projects
and Gists are views or evidence and cannot introduce desired state.

## Context chain

1. `service-backend` owns the catalog, context contract and cross-repository
   graph.
2. `operations/flux-federation/authority.yaml` supplies autonomous GitOps
   repositories; it is not duplicated in the context declaration.
3. Every `module.yaml` supplies a product system, service, website, PRD,
   research or supporting repository plus its explicit module dependencies.
4. A repository-root `AGENTS.md` remains authoritative for that repository.
   Generated module `MEMORY.md` adds platform context but never replaces it.
5. A task starts from a reviewed base revision in a dedicated worktree. The
   optional `.re8ch/task-context.json` is ephemeral handoff metadata and must
   not be committed.

## Commands

```sh
python operations/workspace-controller/workspace.py doctor
python operations/developer-context/context.py doctor
python operations/developer-context/context.py index --check
python operations/developer-context/context.py wiki --check
python operations/developer-context/context.py task-context \
  --module service-backend --objective "Describe the task"
python operations/developer-context/context.py worktree-create \
  --branch codex/example-task --path /workspaces/re8ch-tasks/example-task \
  --module service-backend --objective "Describe the task"
```

Create task worktrees with the repository's native worktree policy. A
Codespace is a reproducible GitHub-hosted developer machine, not a persistent
controller. It may create additional task worktrees beneath `/workspaces`, but
the checkout used as the base must stay clean and no task may mutate another
repository implicitly.

## GitHub collaboration surfaces

- **Wiki:** rendered navigation and explanations tied to a source commit. A
  Wiki page may link to declarations but is never an authority.
- **Projects:** work state, ownership and evidence links. Project fields do not
  replace module or GitOps declarations.
- **Gists:** disposable non-secret reproduction snippets. Secret Gists are
  unlisted, not private, so credentials and restricted topology are forbidden.
- **Issues and pull requests:** discussion and reviewed proposals. Stable
  conclusions graduate into repository declarations or documentation.

External mutations follow a declaration, plan, apply and read-back sequence.
The authenticated GitHub App or user token must have only the repository and
surface permissions required for that operation.

Files under `operations/developer-context/wiki/` are the reviewed Wiki
projection. Publishing them to the enabled `service-backend.wiki.git`
repository is a separate, explicit mutation after the source PR is merged.
