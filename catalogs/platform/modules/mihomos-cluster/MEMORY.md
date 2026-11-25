# mihomos-cluster Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `platform`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/public-edge/mihomos-cluster.git` (`main`)
- Catalog source: `catalogs/platform/modules/mihomos-cluster/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `kubernetes-network-controller`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `mihomos`, `kubectl`
- Deployments: `mihomos`
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/platform/knowledge/resources.yaml
- Catalog overview: Platform Cluster desired state, operations tooling, networking, storage and developer tooling.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 多集群声明与访问边界 - `re8ch` 主集群和 `re8ch-atlantic` 等子集群是独立 Kubernetes 权威。每个集群 分别拥有 API 入口、CA、OIDC audience、GitHub Environment、RBAC、SSA 状态和 故障域；ReleaseContract 的 `target.cluster` 必须选择对应执行 profile。 - 主集群 Cloud SSA 优先使用多入口 `kube-api.service.re8ch.com`； `kube-api.re8ch.com` 仅作临时回退。Atlantic 使用自己的 API 入口，不经主集群

## Repository synopsis

Mihomos Cluster Mihomos Cluster is a Kubernetes egress fabric in which every labelled node consumes a node-local Mihomo proxy and selected nodes also provide healthy CN or Global exits. It is designed to complement Cilium routing: Cilium carries cluster traffic, while Mihomo applies destination geography policy to traffic leaving the cluster. Install Label consumers and providers before installation: CN consumers send international destinations to an available Global provider; Global consumers send Chinese destinations to an available CN provider. Private and cluster CIDRs remain direct. The public identity lookup URL also bypasses Mihomo so telemetry observes the node's real upstream route. Components - `mihomos-node-cn` and `mihomos-node-global`: node-local consumers on port 17891. - `mihomos-cn-exit` and `mihomos-global-exit`: Ready-only provider candidates. - `mihomos-status-agent`: per-node public identity and reachability telemetry.

Existing repository AGENTS: `no`

## Multi-cluster delivery context

Treat `re8ch` and `re8ch-atlantic` as independent Kubernetes authorities.
A ReleaseContract targets one cluster-specific API, CA, OIDC audience, GitHub Environment,
RBAC identity and SSA state; AmongCluster never proxies Kubernetes writes.
The main cluster prefers `kube-api.service.re8ch.com`; Atlantic uses its own API transport.
Share bounded service identity, endpoint, health and path facts through AmongCluster, and
prefer PublicEdge service names for external and internal consumers.
Local Flux writers are permanently retired and forbidden; the bounded cloud SSA executor
is the only runtime writer after signed PR and CI review.

## R640 public network authority

Source of truth: `operations/network/manifests/network/public-mesh/inventory.json`
is a pinned restricted reference. Resolve it using the authenticated runtime API
in `contracts/network-topology/README.md` before any public IP, route, Gateway,
listener, DNAT, eBGP, overlay or dummy-interface change. Routing/service identities
are not directly attached public endpoints. Preserve the declared provider-owned
authority and fallback distinction. Never invent a host route or promote a
fallback when the restricted source is unavailable; stop and restore access.
