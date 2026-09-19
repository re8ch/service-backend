# baozou-ppt-studio Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `baozou-ppt-studio`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/baozou-ppt-studio.git` (`main`)
- Catalog source: `catalogs/baozou-ppt-studio/modules/baozou-ppt-studio/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `presentation-product`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `npm`, `python`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/baozou-ppt-studio/knowledge/resources.yaml
- Catalog overview: Baozou PPT Studio
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

暴走 PPT 动漫工坊 一个纯前端的 PPT 动漫剪辑排版原型，适合把口播、会议梗、产品吐槽或短剧情快速拆成分镜。 - Codex 分镜生成器：输入剧情提示，一键生成 5 幕短动漫结构。 - PPT 画布排版：角色、对白框、字幕条、爆点贴和动势线可拖拽调整。 - 时间线：点击切换分镜，支持自动预览播放。 - 素材导入：本地图片可作为图层加入画布。 - 导出：当前分镜导出 PNG，完整项目导出 JSON。 - Flash 风格示例：内置一套暴走闪切短片，并可导出为独立 HTML 动画。 打开 `http://127.0.0.1:4278`。 Remotion 视频插件 视频渲染代码独立放在 `video/`，读取与编辑器导出兼容的 Storyboard JSON，不会改变现有编辑器。 成片输出到 `output/ai-startup-theater.mp4`。`pnpm video:audio` 会用代码重新生成样片所需的原创 WAV 音效。 Date Damage 自研短视频引擎 打开 `http://127.0.0.1:4278/creator.html` 进入英文 Dating Meme 工作台。 - 4 套喜剧结构、4 个固定角色和 3 个 hook 变体。 - 自研 Story IR、导演编译器、逐帧求值器与 Canvas 2D 渲染器。 - TikTok、YouTube Shorts、B站竖屏和 16:9 输出配置。 - 同一剧本可切换 15–25 秒测试版与 60 秒以上剧情版。 - Canvas 录制后由本地 FFmpeg 转为 H.264/AAC MP4，不经过 Remotion。 - 30 天实验面板使用浏览器本地存储，不自动发布或抓取平台内容。 OpenAI 是可选适配器。复制 `.env.example` 的变量到运行环境并填入 API key 与模型名即可启用；未配置时会自动使用本地原创模板和程序化角色。

Existing repository AGENTS: `no`
