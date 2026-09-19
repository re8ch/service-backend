# network-decomplexer-ios Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `network-decomplexer`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/Woomou/network-decomplexer-ios.git` (`main`)
- Catalog source: `catalogs/network-decomplexer/modules/network-decomplexer-ios/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `ios`; lifecycle: `incubating`
- Knowledge: `README.md`
- Tools: `xcodebuild`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/network-decomplexer/knowledge/resources.yaml
- Catalog overview: Network Decomplexer
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

Start using NetBird at <a href="https://netbird.io/pricing">netbird.io</a> See <a href="https://netbird.io/docs/">Documentation</a> Join our <a href="https://join.slack.com/t/netbirdio/shared_invite/zt-vrahf41g-ik1v7fV8du6t0RwxSrJ96A">Slack channel</a> NetBird iOS & tvOS Client The NetBird iOS/tvOS client allows connections from mobile devices running iOS 14.0+ and Apple TV running tvOS 17.0+ to private resources in the NetBird network. Install You can download and install the app from the App Store: [<img src="https://developer.apple.com/assets/elements/badges/download-on-the-app-store.svg" alt="Download on the App Store" height="80">](https://apps.apple.com/app/netbird-p2p-vpn/id6469329339) Screenshots Code structure The code is divided into 4 parts: - The main netbird Go code, included as a git submodule at `/netbird-core` from the [NetBird](https://github.com/netbirdio/netbird) repo, compiled into an xcframework. This contains most of the client logic.

Existing repository AGENTS: `no`
