# headlamp-advanced-fabric Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `platform`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/advanced-fabrics/headlamp-advanced-fabric.git` (`main`)
- Catalog source: `catalogs/platform/modules/headlamp-advanced-fabric/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `headlamp-plugin`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `npm`, `kubectl`
- Deployments: none declared
- Dependencies: `advanced-fabric`

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/platform/knowledge/resources.yaml
- Catalog overview: Platform Cluster desired state, operations tooling, networking, storage and developer tooling.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 多集群声明与访问边界 唯一权威契约是 [`operations/network/docs/multicluster-federation-contract.md`](../../operations/network/docs/multicluster-federation-contract.md)。 任何设计和故障记录都必须明确是集群控制 federation、PublicEdge 公共服务 federation、AmongCluster 私有服务 federation，还是应用 federation；不得用未限定的 “federation”混合不同层次的成功信号。

## Repository synopsis

Headlamp Advanced Fabric Standard Headlamp plugin for inspecting Advanced Fabric node datapath mode, FRR/BGP/BFD health, kernel ECMP routes and per-node path decisions. Build The plugin expects `networking.re8ch.com/node-status=true` ConfigMaps in `kube-system`, produced by the Advanced Fabric Helm chart. Penrose Triangle Observer The `Penrose Triangle` sidebar page is a read-only scheduling observability surface. It shows NodeProfile or projected node labels, allocatable capacity, installed kube-scheduler profiles and plugins, observed scheduler assignments, and WorkloadTriangle Desired/Actual state. If the scheduling CRDs are not yet installed, the Kubernetes Node, Pod and scheduler ConfigMap views remain available. Kubernetes does not persist every scheduler scoring candidate. The first version therefore distinguishes declared policy and final Pod binding from a future simulator/observer evidence feed instead of presenting inferred scores

Existing repository AGENTS: `no`

## Multi-cluster delivery context

Treat `re8ch` and `re8ch-atlantic` as independent Kubernetes authorities.
A ReleaseContract targets one cluster-specific API, CA, OIDC audience, GitHub Environment,
RBAC identity and SSA state; AmongCluster never proxies Kubernetes writes.
The main cluster uses `kube-api.service.re8ch.com`; Atlantic uses
`kube-api.atlantic.service.re8ch.com`. PublicEdge is the default service path for external
and internal consumers. AmongCluster is only a reviewed, SPIFFE-bound private service path.
Application federation requires an explicit versioned API, event, replication or data contract.
`Grant Active` is authorization intent, not health: verify exact target/RBAC, peer/session or
PublicEdge transport, and a functional probe from the real consumer identity before acceptance.
Never widen RBAC or grants to compensate for endpoint, transport or session failure.
Local Flux writers are permanently retired and forbidden; the bounded cloud SSA executor
is the only runtime writer after signed PR and CI review.
Normative contract: `operations/network/docs/multicluster-federation-contract.md`.

## R640 public network authority

Source of truth: `operations/network/manifests/network/public-mesh/inventory.json`
is a pinned restricted reference. Resolve it using the authenticated runtime API
in `contracts/network-topology/README.md` before any public IP, route, Gateway,
listener, DNAT, eBGP, overlay or dummy-interface change. Routing/service identities
are not directly attached public endpoints. Preserve the declared provider-owned
authority and fallback distinction. Never invent a host route or promote a
fallback when the restricted source is unavailable; stop and restore access.
