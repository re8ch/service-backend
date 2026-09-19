# director-engines Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `baozou-ppt-studio`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/director-engines.git` (`main`)
- Catalog source: `catalogs/baozou-ppt-studio/modules/director-engines/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `rendering-engine`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `python`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/baozou-ppt-studio/knowledge/resources.yaml
- Catalog overview: Baozou PPT Studio
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

Director Engines `Scene IR -> Engine Adapter`。上层 AI 只生成场景 JSON，不需要了解 GMod Lua。 GMod 快速开始 目标主机默认是 SSH 别名 `amdwsl-zt`，GMod 默认位于： `D:\SteamLibrary\steamapps\common\GarrysMod\garrysmod` 安装或更新 addon： 安装后需要重启 GMod 或重新载入地图。进入单人游戏后查询状态： 提交场景并自动录制 demo： 逐帧渲染并准备编码： 直接请求 Source `startmovie`（仅在当前 GMod 构建支持时）： 停止当前任务： 可通过 `--host` 和 `--gmod-root` 覆盖目标；也可设置 `GMOD_DIRECTOR_HOST`、`GMOD_REMOTE_ROOT`。 Scene IR 每个 beat 是一个镜头。未写 `startSec` 时，镜头自动首尾相接。 - `preset`: `closeup`、`medium`、`wide`、`over_shoulder`、`low`、`high` - `movement`: `static`、`slow_push_in`、`pull_out` - 精确控制可提供 `from`、`to`，结构为 `{x,y,z,pitch,yaw,roll}` - `target` 指向 actor id；`durationSec` 控制插值时间；`fov` 控制视角 人物动作 - 内置动作别名：`idle`、`stand`、`sit`、`walk`、`run` - `sequence` 可直接指定模型自带的 Source sequence，优先于 `action` - 同一个 actor id 会被复用，后续镜头可更新位置和动作 - 动作是否存在取决于具体模型；不存在时实体仍会生成，但不会播放该动作 - 字符串形式适合镜头开始即显示的单句对白 - 数组形式支持每句的 `atSec`、`durationSec`、`actorId`、字幕和音频 - `audio` 是相对于 `garrysmod/sound` 的路径

Existing repository AGENTS: `no`
