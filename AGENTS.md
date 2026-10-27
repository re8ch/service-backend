# RE8CH Service Backend Agent MEMORY

This repository is the GitHub-first collaboration and desired-state control plane for catalog, locked workspace revisions, GitOps declarations, infrastructure inventory, PluginDefinitions and Agent context. GitHub is a coordination/event bus, not the Kubernetes manager. Product source stays in its independent GitHub repository; Kubernetes is runtime authority, cloud GitHub Actions and the bounded SSA executor own release reconciliation, Headlamp is the inspection GUI, and raw telemetry remains outside Git.

Before work:

1. Run `python operations/workspace-controller/workspace.py doctor`.
2. Treat `/Volumes/A1/service-backend` as the read-only local anchor for `github/main`. Do not edit, stage, commit, merge, rebase, or develop in that checkout.
3. Create a task-specific worktree from `github/main` before changing repository files. Keep one concern per branch and remove the worktree after its pull request is merged or intentionally closed.
4. Use `workspace context --module <id>` or read `catalogs/<catalog>/modules/<module>/MEMORY.md`.
5. Catalog project source is referenced by gitlinks under `catalogs/*/modules/*/source`. On Macmini, work in each project's existing `/Volumes/A1` repository or an isolated worktree owned by that repository; do not create or rely on a unified RE8CH workspace.
6. On R640, create task worktrees only at the lock SHA. Preserve patches/artifacts before cleanup.
7. Never commit credentials, decrypted SOPS files, local symlinks, runner tokens or product worktree changes here.

GitHub-hosted Enterprise runners are the preferred CI/build workers. Macmini with `/Volumes/A1` mounted handles host-bound audits; R640 remains the cluster mirror/worktree and controlled operations fallback. Cluster writes use the Cloud Platform MCP path first. All GitOps changes require signed commits, successful GitHub Actions and a pull request; Cloud delivery verifies reviewed revisions and immutable artifact signatures before applying them. GitLab is a read-only historical archive and must not become a second collaboration authority.

Generated module MEMORY is reviewable desired context. Repository-root AGENTS.md remains authoritative when a product supplies one; generated context never replaces or injects repository instructions.

## GitHub-native governance and delivery

- Prefer GitHub's native Rulesets, Custom Properties, Environments, Secrets, Variables, Webhooks, Checks and pull requests for repository governance and delivery coordination. Check current official documentation and the installed Apps' actual permissions before designing a replacement script or service. Use Codespaces when an interactive development workspace is needed, not as a persistent reconciliation controller.
- Keep non-secret desired configuration as reviewable, static declarations in Git. Reuse an existing declaration before introducing another source of truth. Apply reviewed configuration with `gh api --input <file>` or the dedicated `gh` command, then read back the effective state and record the result. GitHub enforces server-side rules; storing a declaration alone does not enforce or continuously reconcile it.
- GitHub Apps are the primary automation identities; `gh` is an execution client. Use a short-lived installation token with the required permissions and repository scope. Preserve the existing Observer, Maintenance and Executor responsibilities; do not grant broad permissions or ruleset bypass to make an operation succeed. An App's own webhook configuration requires an App JWT rather than an installation token.
- Manage Rulesets and repository Webhooks through `gh api`; manage Custom Property definitions at organization scope and repository values through the appropriate API. Use `gh secret set` and `gh variable set` for repository, organization or Environment settings. Properties classify repositories; Variables hold non-secret configuration; neither is a secret store. Prefer an existing App event subscription over duplicate repository hooks when it serves the same consumer and purpose.
- Commit only secret names, purposes, scopes and rotation requirements. Secret values and App private keys stay in the approved credential system or GitHub Secrets and must not appear in Git, command arguments, logs or generated context. Preserve the Re8ch plugin boundary for platform credential provisioning and cluster operations.
- Build a release artifact once per source revision and build configuration, retain its immutable digest and provenance, and reuse that artifact through verification and deployment. Update the minimal desired-state reference through a signed PR. Do not rebuild identical artifacts, create redundant bundles, or introduce another promotion step merely to move the same digest. Reuse the existing cloud SSA executor to reconcile approved immutable artifacts. Local Flux controllers are retired; do not restore them as a migration fallback or add a second build planner.
- Actions may perform necessary build, validation and bounded API application work. Before adding a Python planner, polling loop, credential synchronizer or orchestration service, document the concrete native-capability gap and the smallest required implementation in the PR. Prefer deleting redundant stages and using existing GitHub primitives. Verify success from both GitHub checks and the actual runtime; a merged PR alone is not deployment evidence.

## Re8ch plugin organization boundary

The `Re8ch` plugin signs in to the **Re8ch Admin** organization and is for platform and system infrastructure. The `Re8ch Review Demo` plugin signs in to the separate **Re8ch** organization (`tenantRef: re8ch`) and is the application tenant surface. The names are similar, but their organization memberships, user IDs, credentials, grants, and resource ownership are distinct. A person or email visible in Re8ch Admin is not automatically an active member of Re8ch. Never use an Admin organization identity to create or repair a Re8ch tenant application service, or reactivate Admin membership as a shortcut for a Re8ch tenant failure. Resolve the authenticated tenant and verify active membership in that same organization before selecting an identity for a provider-backed service.

Product applications and their tenant-facing service instances are intended to move under the Re8ch organization; Re8ch Admin should retain only system infrastructure. Inventory each application and its current dependencies before migration, then use reviewed GitOps changes and the applicable tenant plans. Do not infer that every current Admin resource is safe to move in one operation.

## RE8CH self-service plugin scope

The `re8ch-tenant` self-service plugin may expose both tenant tools and an administrator platform tool catalog for the authenticated session. Discover its current capabilities and platform catalog before concluding that an application such as AnyCam is inaccessible. The ordinary tenant APIs may be limited to the `re8ch` tenant while a separately published admin tool can inspect or operate other tenant applications. Use only the exact tools and schemas returned by the current catalog; their server-side authorization determines the effective scope. Do not infer cross-tenant write access from a tenant API failure or from a user-supplied tenant name, and do not bypass the plugin with local credentials. For material changes, follow the plugin's plan-before-write workflow and this repository's GitOps PR rules.

## GitOps checkout invariant

- `/Volumes/A1/service-backend` is the canonical local `github/main` anchor. Keep it clean, checked out on `main`, tracking `github/main`, and physically read-only outside `.git`.
- Do not edit files or run mutating Git commands from the anchor. Fetching and a clean fast-forward sync are the only permitted maintenance operations; relock the checkout immediately afterward.
- Start every change from a dedicated worktree based on current `github/main`. Use one concern per branch, signed commits, successful GitHub Actions, and a pull request.
- Before removing a dirty or divergent worktree, preserve it as a patch, stash, bundle, or quarantine checkout. Remove clean task worktrees and their branches after merge or intentional closure.
- Read `operations/workspace-controller/GITOPS-WORKTREE-POLICY.md` before GitOps repository writes. Cluster emergency writes do not authorize bypassing this Git history and review invariant.

## R640 public network authority invariant

Source of truth: `operations/network/manifests/network/public-mesh/inventory.json`
is a pinned restricted reference. Resolve it using the authenticated runtime API
in `contracts/network-topology/README.md` before any public IP, route, Gateway,
listener, DNAT, eBGP, overlay or dummy-interface change. Routing/service identities
are not directly attached public endpoints. Preserve the declared provider-owned
authority and fallback distinction. Never invent a host route or promote a
fallback when the restricted source is unavailable; stop and restore access.
