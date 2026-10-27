# user-hub Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `platform`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/user-hub.git` (`main`)
- Catalog source: `catalogs/platform/modules/user-hub/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `static-web-application`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `docker`
- Deployments: `kubernetes/auth/re8ch-user-center`
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. Start changes in a task-specific worktree based on GitHub `main`; use signed commits, successful GitHub Actions, and a pull request. Keep product source in the User Hub repository and keep only immutable image references and deployment declarations in service-backend.

## Product context

RE8CH User Hub is the unified personal center for digital identity, application and infrastructure connections, organization and role access, API keys and service accounts, security, preferences, activity, and user resources. It preserves the existing shared-auth APIs and intentionally keeps account workflows compact.

## R640 public network authority

Source of truth: `operations/network/manifests/network/public-mesh/inventory.json`
is a pinned restricted reference. Resolve it using the authenticated runtime API
in `contracts/network-topology/README.md` before any public IP, route, Gateway,
listener, DNAT, eBGP, overlay or dummy-interface change. Routing/service identities
are not directly attached public endpoints. Preserve the declared provider-owned
authority and fallback distinction. Never invent a host route or promote a
fallback when the restricted source is unavailable; stop and restore access.
