# netbird-core Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `network-decomplexer`
- Ownership: `external`; write policy: `read-only`
- Repository: `https://github.com/netbirdio/netbird.git` (`main`)
- Catalog source: `catalogs/network-decomplexer/modules/netbird-core/source` (submodule)
- Metadata policy: `control-plane-overlay`
- Project type: `upstream-dependency`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `go`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/network-decomplexer/knowledge/resources.yaml
- Catalog overview: Network Decomplexer
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 多集群声明与访问边界 唯一权威契约是 [`operations/network/docs/multicluster-federation-contract.md`](../../operations/network/docs/multicluster-federation-contract.md)。 任何设计和故障记录都必须明确是集群控制 federation、PublicEdge 公共服务 federation、AmongCluster 私有服务 federation，还是应用 federation；不得用未限定的 “federation”混合不同层次的成功信号。

## Repository synopsis

Start using NetBird at <a href="https://netbird.io/pricing">netbird.io</a> See <a href="https://netbird.io/docs/">Documentation</a> Join our <a href="https://docs.netbird.io/slack-url">Slack channel</a> or our <a href="https://forum.netbird.io">Community forum</a> 🚀 <a href="https://netbird.io/careers">We are hiring! Join us at https://netbird.io/careers</a> > ### 🤖 NetBird Agent Network (Beta) > Identity-aware access control for AI agents — keyless access to LLM APIs and private > resources over the encrypted NetBird tunnel. See [`agent-network/`](agent-network/) or > read the docs at **[netbird.ai](https://netbird.ai)**. **NetBird combines a configuration-free peer-to-peer private network and a centralized access control system in a single platform, making it easy to create secure private networks for your organization or home.** **Connect.** NetBird creates a WireGuard-based overlay network that automatically connects your machines over an encrypted tunnel, leaving behind the hassle

Existing repository AGENTS: `yes`
