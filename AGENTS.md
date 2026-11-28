# RE8CH Service Backend Agent MEMORY

This repository is the GitHub-first collaboration and desired-state control
plane for catalogs, locked revisions, GitOps declarations, infrastructure
inventory, PluginDefinitions and Agent context. Kubernetes is runtime authority;
GitHub Actions and the bounded cloud SSA executor own delivery execution.
The bounded cloud SSA executor is the permanently selected write path; local
Flux writers are forbidden.

## Start work

1. Run `python operations/workspace-controller/workspace.py doctor`.
2. Treat `/Volumes/A1/service-backend` as the read-only `github/main` anchor.
3. Read `operations/workspace-controller/GITOPS-WORKTREE-POLICY.md`.
4. Create one task worktree from current `github/main` for one Issue and concern.
5. Load module context with `workspace context --module <id>` or its `MEMORY.md`.
6. Preserve credentials in approved secret systems and non-secret intent in Git.

Macmini handles `/Volumes/A1` host-bound work. R640 worktrees use the catalog
lock SHA. GitHub-hosted Enterprise runners are the preferred CI/build workers.

## Rich-context delivery

```text
Project → parent Issue → Issue → branch/worktree → commits → PR
        → checks → runtime evidence → Project status
```

- Put every Issue in the relevant Project; use `Others` for unresolved ownership.
- Give each Project item type, lifecycle, status, start date and completion or
  target date. Use parent/sub-issue relationships and Milestones for phases.
- Name branches for the Issue outcome and keep one concern per worktree.
- Make signed commits small, coherent and traceable to the Issue.
- Close the Issue from the PR and update Project status from verified outcomes.
- Build an artifact once for each source revision and build configuration, then
  reuse its immutable digest through verification and delivery.
- Run each CI test class once per revision; a changed revision starts a new run.

Use `.github/PROJECT_CONTEXT_TEMPLATE.md` for Project structure and views.

## Code and documentation

- Prefer the smallest implementation that uses existing GitHub and platform
  primitives.
- Keep comments for non-obvious invariants, safety boundaries and external
  contracts. Let names, types and tests explain ordinary control flow.
- Remove obsolete code, duplicate adapters, stale examples and commentary when
  changing their owning path.
- Put Kubernetes output, incident timelines, experiments, migration stories and
  user-facing examples in Issues, Discussions, Wiki or observability systems.
- Keep root documentation as a short map to authoritative contracts and runbooks.
- Record the native-capability gap in the Issue before adding a controller,
  planner, polling loop or credential synchronizer.

## GitHub and runtime authority

GitHub Rulesets, Custom Properties, Environments, Secrets, Variables, Webhooks,
Checks, Issues, Projects and pull requests provide repository governance and
delivery coordination. GitHub Apps are automation identities; `gh` is an
execution client. Apply reviewed declarations, read back effective state and
record the result in the Issue.

The Re8ch plugin is the tenant self-service entry. Resolve the active tenant,
discover its current capability schema and use the exact published operation.
`admin` and `re8ch` are separate organizations with separate membership,
credentials, grants and ownership.

Product source stays in its own repository. This repository stores reviewed
references and platform contracts. Generated module MEMORY supplements the
product repository's root instructions.

## Critical infrastructure context

- `operations/flux-federation/authority.yaml` defines repository delivery
  authority. GitHub Actions and the bounded cloud SSA executor are the runtime
  write path.
- `operations/network/manifests/network/public-mesh/inventory.json` defines the
  R640 public-delivery authority. Resolve its authenticated topology through
  `contracts/network-topology/README.md` for network work.
- Runtime health combines GitHub checks with actual runtime evidence.

## Multi-cluster federation invariant

`operations/network/docs/multicluster-federation-contract.md` is the single
normative contract. Qualify federation as cluster control, public service,
private AmongCluster service or application federation; never infer success in
one plane from another. Cloud SSA writes directly to the named cluster through
its cluster-specific API. PublicEdge is the default cross-cluster service path;
AmongCluster is only a bounded private service path and never a Kubernetes
control proxy. `Grant Active` is authorization intent, never completion
evidence; exact target/RBAC, transport/session and real-consumer functional
probes are all required before declaring recovery.
