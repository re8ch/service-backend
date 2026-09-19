# garageband-mcp-site Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `garageband`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/garageband-mcp-site.git` (`main`)
- Catalog source: `catalogs/garageband/modules/garageband-mcp-site/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `web`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `npm`
- Deployments: none declared
- Dependencies: `garageband-mcp`

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/garageband/knowledge/resources.yaml
- Catalog overview: GarageBand
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

garageband.mcp Cloudflare Pages 静态控制台、Worker/Durable Object 控制层，以及安装在单台 Mac mini 上的常驻 GarageBand Agent。 Mac mini Agent 安装 目标 Mac mini 必须安装 GarageBand 和 Node.js 22。将 `agent` 目录复制到 Mac mini，然后运行： 部署机上的一次性安装令牌可通过以下命令读取： 安装程序会将设备凭证写入 Mac mini 的 Keychain，并安装 `com.garageband-mcp.agent` LaunchAgent。随后在系统设置中给 Node 授予辅助功能、自动化、屏幕录制和麦克风权限。 若已有 GarageBand MCP stdio 服务，编辑 `~/Library/Application Support/garageband-agent/config.json`，将 `mcpCommand` 设置成命令数组，例如： Agent 会通过 MCP `tools/list` 自动将工具同步到网页，并通过 `tools/call` 执行操作。 管理员控制令牌保存在部署机 Keychain： 卸载 Agent：`agent/uninstall.sh`。卸载会保留配置、日志和音乐工程。 - `npm run deploy:control`：部署 Worker 与 Durable Object。 - `npm run migrate:control`：应用 D1 审计表迁移。 - `npm run deploy:cf`：静态导出并部署 Cloudflare Pages。 - `npm run test:agent`：验证 Agent 与安装脚本语法。 生产资源：D1 `garageband-mcp`、R2 `garageband-mcp-artifacts`、Worker `garageband-mcp-control`、Pages `garageband-mcp`。

Existing repository AGENTS: `no`
