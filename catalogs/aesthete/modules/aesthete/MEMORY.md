# aesthete Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `aesthete`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/aesthete.git` (`main`)
- Catalog source: `catalogs/aesthete/modules/aesthete/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `visual-product`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `python`, `blender`, `git-lfs`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/aesthete/knowledge/resources.yaml
- Catalog overview: Aesthete RE8CH visual and anatomy product workspace.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

Aesthete Aesthete 是一个早期研发项目，目标是探索桌面家用机器人/无人机辅助用户化妆的可行性。 权威开发环境 高算力 Unreal Engine / MetaHuman 开发与最终验收统一在 AMD Windows Worker 上执行，不以 Mac mini 的 UE 安装状态作为项目总阻塞条件： | 职责 | 节点与路径 | | --- | --- | | 权威 UE 5.8 / GPU 构建与验收 | `amdwsl-zt` 对应 Windows，`D:\UE5.8\UE_5.8` | | Worker 本地工程缓存 | Windows `D:\AestheteWorker` | | 控制、编辑、轻量检查与结果查看 | Mac mini（当前仓库） | | 大文件发布与结果同步 | R640 NAS `/srv/nas/shared` | | Mac NAS 挂载 | `/Users/harryneo/R640-NAS`（SFTPGo WebDAV，用户 `woomoo`） | | AMDWSL NAS 挂载 | `/Volumes/R640-NAS`（NFS 4.1，以 `woomoo` 访问） | 正常命令通道使用 `ssh amdwsl-zt`；Mac 的 ZeroTier 直连路由不可用时，使用 `ssh -J r640 amdwsl-zt`。ProxyJump 仍只传命令和小型诊断输出，不用 SSH/rsync 批量搬运 UE Content、MetaHuman、扫描或渲染资产。大文件必须经 NAS 发布镜像 增量同步，且 UE 只能从 Windows 本地 `D:\AestheteWorker` 构建和运行，不能直接 从 SMB/NFS 工程运行。 部署入口与故障排查见 [`docs/amd-nas-deployment.md`](docs/amd-nas-deployment.md)；AMD UE 验收流程见 [`docs/amd-unreal-makeup-workflow-validation.md`](docs/amd-unreal-makeup-workflow-validation.md)。

Existing repository AGENTS: `no`
