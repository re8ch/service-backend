# 2woodblog Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `2woodblog`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/Woomou/2woodblog.git` (`main`)
- Catalog source: `catalogs/2woodblog/modules/2woodblog/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `web`; lifecycle: `maintenance`
- Knowledge: `README.md`
- Tools: `npm`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/2woodblog/knowledge/resources.yaml
- Catalog overview: 2wood Blog
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

2woodblog 2woodblog 是一个基于 SvelteKit 的博客项目，通过 Notion 获取内容并分别部署到国际站 [2wood.cn](https://2wood.cn) 和中国大陆优化站 [www.2wood.cn](https://www.2wood.cn)。 - SvelteKit 架构的前端应用位于 `svelte-blog/` 目录 - 通过脚本从 Notion 数据库拉取文章内容 - 支持全球与中国双版本构建与部署 - 提供上传脚本将静态资源同步到 Cloudflare R2 与腾讯云 COS - `svelte-blog/worker.js` 可作为简单的 Cloudflare Worker 代理 快速开始 1. 复制 `.env.example` 为 `.env`，并填入 Cloudflare、Notion 及存储服务的凭据 2. 安装依赖并构建： `build:china` 会在 `zh-build/` 中生成资源域名替换后的静态文件，并可使用 `upload-to-cos.js` 等脚本将资源同步至国内 CDN。 项目结构 - `svelte-blog/` – SvelteKit 源码、部署脚本与 Worker - `.github/workflows/main.yml` – 构建并部署站点的 CI 工作流

Existing repository AGENTS: `yes`
