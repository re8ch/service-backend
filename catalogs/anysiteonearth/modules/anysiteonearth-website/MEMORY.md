# anysiteonearth-website Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `anysiteonearth`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/anysiteonearth-website.git` (`main`)
- Catalog source: `catalogs/anysiteonearth/modules/anysiteonearth-website/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `web`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `npm`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/anysiteonearth/knowledge/resources.yaml
- Catalog overview: Any Site on Earth Website generation product, backend and public frontend.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

Any Site on Earth (RE8CH 子站) Static site for `anysiteonearth.re8ch.com`, `anysite.re8ch.com`, and `cn.anysiteonearth.re8ch.com`，归属于 RE8CH 网站家族的地理空间子项目。 - Source/output: `dist/` - Base domains: `anysiteonearth.re8ch.com`, `anysite.re8ch.com` - Brand asset source of truth: `https://brand-assets.re8ch.com` 或直接执行： Deployment targets: - Global: Cloudflare Pages project `anysiteonearth` at `https://anysiteonearth.re8ch.com`, `https://anysite.re8ch.com`, and `https://anysiteonearth.pages.dev` - China: Tencent COS bucket `anysiteonearth-1301339749` in `ap-chongqing`, accelerated by EdgeOne at `https://cn.anysiteonearth.re8ch.com` 品牌资产 产品图标由 `brand-assets` 统一治理，本仓库不保存长期 logo 源文件。 默认使用全球资产域： 中国区构建可设置： Required GitHub Actions secrets: 代码与说明 本仓库保留了原始页面源码（`src/`）与静态交付产物（`dist/`）： - `src/`: Next.js 源码（交互地图、地形生成、3D 渲染） - `dist/`: 部署到 RE8CH 子站的静态文件

Existing repository AGENTS: `yes`
