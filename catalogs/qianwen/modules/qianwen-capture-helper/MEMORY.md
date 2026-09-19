# qianwen-capture-helper Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `qianwen`
- Ownership: `collaboration`; write policy: `authorized-upstream-pr`
- Repository: `https://github.com/OCGS-with-pattern/Qianwen-capture-helper.git` (`master`)
- Catalog source: `catalogs/qianwen/modules/qianwen-capture-helper/source` (submodule)
- Metadata policy: `control-plane-overlay`
- Project type: `helper`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `npm`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/qianwen/knowledge/resources.yaml
- Catalog overview: Qianwen Collaboration workspace. Writes require an authorized upstream or fork PR.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

短剧猫抓图助手 Android floating capture helper for Short Drama Cat image-to-prompt workflows. Production API calls target `https://api.jz.ynzs.com`, now served by the Go / Nuclio backend in the main repository `nuclio/`. The historical Node.js backend is archived under `deprecated/node-backend/`. Build The first build needs an Android SDK with platform 35 installed. Debug APK output: Product Direction 短剧猫抓图助手用于在 Android 手机上快速截取屏幕中的图片区域，并调用短剧猫后端反推 AI 图片生成提示词。 第一版优先做成独立 Android App，而不是移动浏览器插件： - 悬浮侧边按钮：用户在任意 App 中看到图片时，点按钮进入框选抓图。 - 框选截图：用户手动框出图片区域，App 上传裁剪图。 - 提示词反推：调用现有 `/other/reverseImagePrompt` 接口。 - 结果操作：复制提示词、打开短剧猫图像广场、继续生成。 - 系统分享入口：接收其他 App 分享来的图片或图片 URL，直接反推。 MVP Scope 1. App 登录或访客身份接入短剧猫账号体系。 2. 悬浮窗权限引导与侧边按钮。 3. MediaProjection 主动截屏。 4. 图片框选与裁剪。 5. 调用反推提示词接口。 6. 结果页展示、复制和跳转官网。 7. Android Sharesheet `image/*` / `text/plain` 接收入口。 Current Implementation - `MainActivity`: 权限说明、悬浮按钮开关、访客设备 ID 展示。 - `FloatingButtonService`: 常驻侧边抓图按钮，支持拖动和点击。

Existing repository AGENTS: `no`
