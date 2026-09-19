# qianwen-ios Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `qianwen`
- Ownership: `collaboration`; write policy: `authorized-upstream-pr`
- Repository: `https://github.com/OCGS-with-pattern/QianwenIOS.git` (`master`)
- Catalog source: `catalogs/qianwen/modules/qianwen-ios/source` (submodule)
- Metadata policy: `control-plane-overlay`
- Project type: `ios`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `xcodebuild`
- Deployments: none declared
- Dependencies: `qianwen-app`

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/qianwen/knowledge/resources.yaml
- Catalog overview: Qianwen Collaboration workspace. Writes require an authorized upstream or fork PR.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

Qianwen iOS SwiftUI 版本的荆州短剧 AI 移动端骨架。第一版和微信小程序保持同一套功能闭环： 当前正式后端为主仓库 `nuclio/` 下的 Go / Nuclio 云函数。历史 Node.js 后端已归档到 `deprecated/node-backend/`，iOS 端继续通过 API 域名选择测试或生产环境。 - 手机号登录，复用 `/other/loginPhone` - 项目列表与项目详情素材概览 - 三大广场：文字、图片、视频 - 公共素材与任务队列 - `Bearer` token 登录态，多端共享同一后端数据 当前验证方式 在 `Qianwen-web` 下运行： 打开 Xcode 工程： 脚本会自动优先使用： - `DEVELOPER_DIR` - `/Volumes/A1/Applications/Xcode.app/Contents/Developer` - `/Applications/Xcode.app/Contents/Developer` 检查流程包含两步： - `swift build --target QianwenCore` - `xcodegen generate` - `xcodebuild -project QianwenIOS.xcodeproj -scheme QianwenIOS -destination "generic/platform=iOS Simulator" build` 注意：不要直接在 Xcode 里打开 `QianwenIOS` 文件夹作为 Swift Package 运行 app。Swift Package 只负责 `QianwenCore`，真正可运行的 iOS app 由 `project.yml` 生成的 `QianwenIOS.xcodeproj` 承载。 如果安装了 XcodeGen，也可以直接生成工程： 默认 API 环境是 `https://test.api.jz.ynzs.com`。iOS 端和 Web/小程序共享后端账号与数据，只要登录同一账号，项目、素材、任务和广场结果就是同一份。

Existing repository AGENTS: `no`
