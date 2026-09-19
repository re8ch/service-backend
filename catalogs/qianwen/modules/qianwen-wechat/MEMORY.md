# qianwen-wechat Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `qianwen`
- Ownership: `collaboration`; write policy: `authorized-upstream-pr`
- Repository: `https://github.com/OCGS-with-pattern/Qianwen-wechatminiprogram.git` (`master`)
- Catalog source: `catalogs/qianwen/modules/qianwen-wechat/source` (submodule)
- Metadata policy: `control-plane-overlay`
- Project type: `wechat-miniprogram`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `wechat-devtools`
- Deployments: none declared
- Dependencies: `qianwen-app`

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/qianwen/knowledge/resources.yaml
- Catalog overview: Qianwen Collaboration workspace. Writes require an authorized upstream or fork PR.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

Qianwen 微信小程序 原生微信小程序子项目，第一版覆盖移动端核心闭环：微信登录、项目列表/详情、三大广场、公共素材、任务和个人页。 当前正式后端为主仓库 `nuclio/` 下的 Go / Nuclio 云函数。历史 Node.js 后端已归档到 `deprecated/node-backend/`，小程序环境仍按 API 域名区分测试和生产。 如果还在 `Qianwen-web` 里操作，也可以继续用旧入口，脚本会优先指向同级的 `Qianwen-wechatminiprogram`： 默认使用 `/Applications/wechatwebdevtools.app/Contents/MacOS/cli`，也可以通过 `WX_CLI=/path/to/cli` 覆盖。 上传体验版使用官方 `miniprogram-ci`，会读取本目录下的代码上传私钥： 可选环境变量： - 小程序 AppID 在 `project.config.json` 中配置。 - 后端微信登录需要配置 `WX_MINI_APPID` 和 `WX_MINI_APP_SECRET`。 - 真机预览前，微信后台需要把测试 API 域名加入 request 合法域名：`https://test.api.jz.ynzs.com`。 - 未登录微信开发者工具、或 DevTools 登录态过期时，游客 AppID 会出现 `INVALID_TOKEN`、`access_token missing`、`tourist appid` 等工具层错误，严重时模拟器会卡在白屏并提示 appLaunch 超时。重新登录 DevTools，或替换为真实 AppID 后再清编译缓存。 GitHub 上传建议 - 可以把本目录作为独立 GitHub 仓库上传。 - 不要提交 `project.private.config.json`、`node_modules/` 和编译生成的 `miniprogram/**/*.js`。 - 上传前可以运行：

Existing repository AGENTS: `no`
