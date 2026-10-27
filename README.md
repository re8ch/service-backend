# RE8CH Service Backend

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

## Administrator self-service

Use the authenticated `re8ch-tenant` self-service plugin as the primary entry
for both tenant operations and published platform administration. Its
`platform_tool_catalog` and `platform_tool_call` expose administrator tools to
a verified platform-admin session; ordinary tenant grant and service-class
limits do not reduce that separately authorized administrator scope. The
catalog's exact tool schema and backend authorization remain authoritative.
For PostgreSQL schema work, the administrator-only `postgres_admin_sql` tool
uses the existing Supabase/Pigsty credential inside the cluster. It requires a
reviewed approval ID and returns a plan hash before a write; it never returns
the credential. Run reviewed SQL,
retain the plan and result as migration evidence, and follow the GitOps PR
policy for desired-state changes. Interactive Supabase Studio login is optional
for human inspection, not a prerequisite for agent database migrations.

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

The cluster and platform history formerly stored in
`re8ch/cloud-functions` was imported on 2026-08-24. Its old-to-new commit maps
are maintained under `operations/migrations/cloud-functions-history/`.
