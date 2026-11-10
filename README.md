# RE8CH Service Backend

<!-- gitops-status:start -->
[![Reconcile](https://github.com/re8ch/service-backend/actions/workflows/main-domain-reconcile.yml/badge.svg?branch=main)](https://github.com/re8ch/service-backend/actions/workflows/main-domain-reconcile.yml?query=branch%3Amain)
[![Control plane CI](https://github.com/re8ch/service-backend/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/re8ch/service-backend/actions/workflows/ci.yml?query=branch%3Amain)
[![Security checks](https://github.com/re8ch/service-backend/actions/workflows/github-security-checks.yml/badge.svg?branch=main)](https://github.com/re8ch/service-backend/actions/workflows/github-security-checks.yml?query=branch%3Amain)
[![Policy reviewer](https://github.com/re8ch/service-backend/actions/workflows/github-policy-reviewer.yml/badge.svg?branch=main)](https://github.com/re8ch/service-backend/actions/workflows/github-policy-reviewer.yml?query=branch%3Amain)
[![Receipt dispatch](https://github.com/re8ch/service-backend/actions/workflows/gitops-receipt-observation.yml/badge.svg?branch=main)](https://github.com/re8ch/service-backend/actions/workflows/gitops-receipt-observation.yml?query=branch%3Amain)

These live badges show the latest workflow result on `main`, including scheduled
and manual runs. Reconcile runs can cover a single release; a passing badge is
not proof that every release is healthy or that SSA changed an object. Open the
run for its release scope, accepted digest, comparison, apply and health results.
The [domain observation Project](https://github.com/orgs/re8ch/projects/4) retains release-level evidence;
receipt dispatch and governance checks do not replace runtime health verification.
Private workflow badges require repository access.
<!-- gitops-status:end -->

GitHub-first collaboration and desired state for the RE8CH platform. GitHub
Issues are work/events, pull requests are proposals, signed commits are state
transitions, and Actions/webhooks are reactions. Kubernetes remains the runtime,
Flux the convergence engine, Headlamp the inspection GUI, and raw telemetry
stays in the observability backends.

GitLab is retained only as a read-only historical archive. GitHub is the sole
collaboration and GitOps authority; dual-write collaboration is forbidden.

## Public contribution and release evidence

We intend to share this control-plane code and its non-secret history freely as
a contribution to the software community. Active credentials are excluded from
that publication policy and must remain in the approved secret stores. Public
visibility does not replace the repository's applicable license terms.

[Cloud release view](https://re8ch.github.io/service-backend/) reads GitHub
Checks, Deployments and Flux commit statuses without connecting to the cluster.
Expired or unavailable runtime evidence is shown as unknown.

## Restricted infrastructure context

The versioned topology is restricted. Read `contracts/network-topology/README.md`
for the authenticated runtime API and protected member/App diagnostic workflow.
The public inventory path is an immutable reference, not executable node data.
Do not infer endpoint, route, failover or storage behavior from an old MEMORY.

## Federated Flux control plane

The main `flux-system` is the bootstrap and policy authority, not the universal
workload owner. Four domains run independent Source, Kustomize, Helm and
Notification controller stacks and continue reconciling when the main
`service-backend` Kustomization is paused or broken:

| Domain | Controller namespace | Desired-state repository |
| --- | --- | --- |
| Agent | `flux-agent-system` | `re8ch-ai/agent-gitops` |
| Identity | `flux-identity-system` | `re8ch/identity-gitops` |
| Observability | `flux-observability-system` | `siren-observer/observability-gitops` |
| Network | `flux-network-system` | `pillars-infra/network-gitops` |

The main Flux owns Flux CRDs, autonomous controller lifecycle, namespaces,
global RBAC, shared Gateway authority, and Secret/policy boundaries. Each
autonomous Flux owns only its repository source and domain inventory. Product
runtimes with external users and a high release cadence remain outside Flux;
their slow-moving platform guardrails stay in the appropriate domain.

The machine-readable authority ledger is
[`operations/flux-federation/authority.yaml`](operations/flux-federation/authority.yaml),
and the operational contract is documented in
[`operations/releases/DOMAIN-RECONCILIATION.md`](operations/releases/DOMAIN-RECONCILIATION.md).

## Layout

- `clusters/re8ch`: Flux bootstrap and cluster desired-state components
- `platform`: platform controllers, applications, clients and plugins
- `inventory`: non-secret infrastructure targets
- `plugins`: generated-tool source declarations and policy overlays
- `operations`: automation, host/network operations and control-plane runtimes
- `runbooks`: release, investigation, incident and rollback procedures
- `schemas`: declarative API schemas
- `tests`: policy and contract tests

External frontend/backend repositories use the digest-pinned GitOps deployment
entry point documented in [`runbooks/external-project-deployment.md`](runbooks/external-project-deployment.md).
It renders isolated Kubernetes resources for review without distributing
cluster credentials.

Pure static frontends bypass Harbor and Kubernetes: they publish either their
source directory or compiled output directly to Firebase/shared object storage,
Tencent COS, Aliyun OSS, or Cloudflare with the provider-native CLI/API. The
enforced contract is documented in
[`operations/public-sites/README.md`](operations/public-sites/README.md).

No plaintext credential belongs in this repository. Runtime identities refer to
Kubernetes Secrets or SOPS-encrypted values and never return credential material
to MCP callers.

## Re8ch self-service

The current `Re8ch` plugin is the single entry for tenant self-service. Check
`tenant_access_status` when organization scope matters, discover
`tenant_self_service_capabilities`, and call the exact published operation.
Confirm a real read or plan result before reporting a capability as working.
Tenant Kubernetes tools are scoped to allowlisted kinds and Ready namespaces
owned by the active organization; they are not unrestricted cluster `kubectl`.

Older Cloud Platform connections and the optional `platform_tool_catalog` /
`platform_tool_call` administrator bridge are not prerequisites or health
checks for tenant self-service. A missing administrator entry point does not
mean tenant self-service is down. For database schema work, discover the
currently authorized operation, keep credentials inside the platform, retain
plan and result evidence, and use a reviewed GitOps PR for desired state.

### Re8ch Admin GitHub Enterprise governance

The `admin` organization has one platform-owned singleton service instance for
bounded GitHub Enterprise governance:

- service class: `integration.github.enterprise-governance.v1`
- resource reference: `service://admin/github-enterprise-governance`
- model-callable entry point: the existing `service_instance_update` tool
- credential model: a GitHub App installation token minted inside the provider;
  the App private key and token are never returned to the caller

This is a Re8ch Admin service capability, not a new top-level plugin tool or a
generic GitHub API proxy. It requires the authenticated session to use the
`admin` tenant with the `admin` role, an Active governance grant, and a Ready
singleton instance. Discover both `tenant_self_service_capabilities` and
`service_catalog_list` before use; the catalog must advertise an
`operationContract` whose entry point is `service_instance_update`.

The current contract supports repository and organization Rulesets, repository
and organization Actions Variables, and allowlisted organization settings.
Reads execute immediately. Every create, update, or delete first returns a
preview containing `method`, `path`, and `planHash`; apply only the unchanged
request with `apply: true` and that exact hash, then read back the effective
GitHub state. For example, a repository Ruleset read uses:

```json
{
  "resource_ref": "service://admin/github-enterprise-governance",
  "idempotency_key": "read-service-backend-rulesets-v1",
  "spec": {
    "resource": "repository_ruleset",
    "operation": "list",
    "owner": "re8ch",
    "repo": "service-backend"
  }
}
```

Supported resource and operation pairs are:

| Resource | Operations |
| --- | --- |
| `repository_ruleset` | `list`, `get`, `create`, `update`, `delete` |
| `organization_ruleset` | `list`, `get`, `create`, `update`, `delete` |
| `repository_variable` | `list`, `get`, `create`, `update`, `delete` |
| `organization_variable` | `list`, `get`, `create`, `update`, `delete` |
| `organization` | `get`, `update` for provider-allowlisted fields |

Live acceptance on 2026-11-10 read the effective Rulesets for
`re8ch/service-backend`, read the `re8ch` organization settings, and completed
a repository Variable create/read/delete cycle. That evidence proves these
specific operations and installation permissions; it does not authorize
Secrets, arbitrary REST/GraphQL calls, ruleset bypass, or organizations where
the governance App is not installed. The implementation and rollout contract
are documented in [`platform/apps/tenant-mcp/README.md`](platform/apps/tenant-mcp/README.md#re8ch-admin-github-enterprise-governance-service).

## Federated workspace

`catalogs/` groups independent Git repositories into product catalogs. Every
non-root module is a real Git submodule at `catalogs/<catalog>/modules/<module>/source`.
Each `module.yaml` records ownership, project identity, knowledge, tools and
deployments. `workspace/workspace.lock.yaml` mirrors each gitlink commit for
machine-readable indexing and drift detection.

```sh
python operations/workspace-controller/workspace.py doctor
python operations/workspace-controller/workspace.py scan
python operations/workspace-controller/workspace.py status
git submodule update --init catalogs/platform/modules/cloud-functions/source
python operations/workspace-controller/workspace.py context --module cloud-functions
```

Developers use each concrete A1 project checkout or a project-local Git
worktree. There is no shared RE8CH checkout tree. GitHub remains product source
authority. This repository is the catalog/GitOps authority and its signed GitHub
`main` is the Flux source.

The cross-repository development context is declared in
[`workspace/developer-context.yaml`](workspace/developer-context.yaml) and
rendered to [`indexes/developer-context.json`](indexes/developer-context.json).
It connects this control plane to every catalog module and autonomous GitOps
repository, and defines Codespaces, task worktrees, Wiki, Projects and Gists as
bounded collaboration surfaces. See
[`operations/developer-context/README.md`](operations/developer-context/README.md).

The cluster and platform history formerly stored in
`re8ch/cloud-functions` was imported on 2026-08-24. Its old-to-new commit maps
are maintained under `operations/migrations/cloud-functions-history/`.

## Independent SSA and Observe actions

Reconcile reports SSA execution and runtime observation separately. The SSA step
validates authorization and the signed accepted artifact, performs server-side
dry-run and apply, then records `outcome: applied` and `acceptedDigest`. It does
not claim a healthy runtime. The following read-only Observe step publishes a
separate observation artifact and records healthy, degraded or unknown health.
Runtime health does not fail SSA, lock the release or trigger rollback. Signature,
authorization, field ownership and API write failures remain execution failures.
Legacy `healthyDigest` is only a fallback for older accepted records; new replay
authorization uses the explicitly accepted digest without inventing health proof.
