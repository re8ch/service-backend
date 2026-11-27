# anycam-website Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `anycam`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/anycam-website.git` (`main`)
- Catalog source: `catalogs/anycam/modules/anycam-website/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `web`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `npm`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/anycam/knowledge/resources.yaml
- Catalog overview: Anycam Cross-platform camera product, backend and public website.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 多集群声明与访问边界 唯一权威契约是 [`operations/network/docs/multicluster-federation-contract.md`](../../operations/network/docs/multicluster-federation-contract.md)。 任何设计和故障记录都必须明确是集群控制 federation、PublicEdge 公共服务 federation、AmongCluster 私有服务 federation，还是应用 federation；不得用未限定的 “federation”混合不同层次的成功信号。

## Repository synopsis

Anycam Website Standalone product website for Anycam 任意相机. This repository is reserved for the public product page. Android app icons and source brand assets remain governed by `re8ch/brand-assets`. Brand assets Current Anycam product website icons are published from: Future product pages should support: Do not commit long-lived logo, app icon, Android vector, or motion source files into this website repository. Android launcher source assets remain under `brand-assets/anycam/`; website product icons use `brand-assets/PRODUCTS/anycam/`. Deployment Deployment targets: - Global: `https://anycam.re8ch.com` on Cloudflare Pages project `anycam-website` - China: Tencent COS bucket `anycamwebsite-1301339749` in `ap-shanghai`, accelerated by EdgeOne at `https://cn.anycam.re8ch.com` Required GitHub Actions secrets: Product Site Design Compliance - Global and China builds emit 27 localized pages under `/<locale>/`; `/en/`

Existing repository AGENTS: `no`
