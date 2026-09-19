# ledger-dashboard Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `ledger`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/ledger-dashboard.git` (`main`)
- Catalog source: `catalogs/ledger/modules/ledger-dashboard/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `web-dashboard`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `npm`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/ledger/knowledge/resources.yaml
- Catalog overview: Ledger RE8CH financial product, dashboard and public website.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

Ledger Remote Console The protected `/decision-lab` route is an interactive first-principles laboratory for the 20 deterministic Ledger decision models. Configure `NEXT_PUBLIC_DECISION_API_URL` to point to the authenticated Python decision service. The browser keeps the Ledger JWT in session storage only. The laboratory supports synthetic references and real-ledger mode, candidate fact confirmation, provenance selection, background and assumption evidence, model gating, seven requested model visualizations, and shared immutable run history. It never executes a business action or creates an accounting posting. Runtime A clean full-stack starter running on [vinext](https://github.com/cloudflare/vinext), with optional Cloudflare D1 and Drizzle support. Prerequisites - Node.js `>=22.13.0` Quick Start This starter does not use `wrangler.jsonc`. Included Shape - edit site code under `app/` - `.openai/hosting.json` declares optional Sites D1 and R2 bindings

Existing repository AGENTS: `no`
