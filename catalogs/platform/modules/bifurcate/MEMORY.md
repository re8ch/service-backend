# bifurcate Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `platform`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/bifurcate-systems/bifurcate.git` (`main`)
- Catalog source: `catalogs/platform/modules/bifurcate/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `metering-api`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `python`, `pytest`, `docker`
- Deployments: `kubernetes/observability-ai/birfucate-metering`
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/platform/knowledge/resources.yaml
- Catalog overview: Platform Cluster desired state, operations tooling, networking, storage and developer tooling.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 多集群声明与访问边界 唯一权威契约是 [`operations/network/docs/multicluster-federation-contract.md`](../../operations/network/docs/multicluster-federation-contract.md)。 任何设计和故障记录都必须明确是集群控制 federation、PublicEdge 公共服务 federation、AmongCluster 私有服务 federation，还是应用 federation；不得用未限定的 “federation”混合不同层次的成功信号。

## Repository synopsis

Bifurcate > Experimental source import from `re8ch/cloud-functions` commit `b35cdf1`. > This service is not production-ready: its current API is not tenant-isolated, > and it must not be exposed to untrusted clients or deployed as a shared service. The source, container image and Helm chart are publicly released under Apache-2.0. See [`charts/bifurcate`](charts/bifurcate) for the experimental single-replica install and [`docs/ARTIFACT_HUB.md`](docs/ARTIFACT_HUB.md) for the OCI/Artifact Hub publishing contract. Bifurcate is the purpose-to-consumption resource lineage control plane for agents. It records how homogeneous compute, network, storage, model and context resources become differentiated for a purpose, which dependency chain was invoked, and which concrete execution consumed the result. The execution boundary is a provider-neutral Sandbox Claim. CubeSandbox can fulfill that Claim, but Agent tasks do not bind directly to it. LiteLLM's

Existing repository AGENTS: `no`

## R640 public network authority

Source of truth: `operations/network/manifests/network/public-mesh/inventory.json`
is a pinned restricted reference. Resolve it using the authenticated runtime API
in `contracts/network-topology/README.md` before any public IP, route, Gateway,
listener, DNAT, eBGP, overlay or dummy-interface change. Routing/service identities
are not directly attached public endpoints. Preserve the declared provider-owned
authority and fallback distinction. Never invent a host route or promote a
fallback when the restricted source is unavailable; stop and restore access.
