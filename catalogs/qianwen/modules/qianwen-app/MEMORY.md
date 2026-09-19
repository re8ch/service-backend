# qianwen-app Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `qianwen`
- Ownership: `collaboration`; write policy: `authorized-upstream-pr`
- Repository: `https://github.com/OCGS-with-pattern/Qianwen-app.git` (`master`)
- Catalog source: `catalogs/qianwen/modules/qianwen-app/source` (submodule)
- Metadata policy: `control-plane-overlay`
- Project type: `web-app`; lifecycle: `active`
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

SERVER_IP=192.168.0.105 docker-compose -f docker/docker-compose.local.yml build --no-cache && \ docker-compose -f docker/docker-compose.local.yml up -d &nbsp;|&nbsp; 荆州A短剧工作流 AI短剧工厂 动动手指，小说秒变剧集！ AI剧本 × AI影像 × 极速生成 🔥 > 🚀 **一站式短剧工程**：从文本到角色，从分镜到视频，0门槛全流程AI化，创作效率提升10倍+！ 📁 子路径导航 为方便团队协作与交接，以下是“荆州A短剧工作流”在本仓库的核心子路径： - `README.md`：主仓库说明（后端 + 桌面端 + 部署） - `docs/README.en.md`：主仓库英文说明 - `前端子项目/README.md`：前端子项目说明 - `前端子项目/docs/README.en.md`：前端子项目英文说明 🌟 主要功能 荆州A短剧工作流（jingzhouAI）是一款 AI 工具，能够利用 AI 技术将小说自动转化为剧本，并结合 AI 生成的图片和视频，实现高效的短剧创作。借助荆州A短剧工作流，可以轻松完成从文字到影像的全流程，让短剧制作变得更加智能与便捷。 - ✅ **角色生成** 自动分析原始小说文本，智能识别并生成角色设定，包括外貌、性格、身份等详细信息，为后续剧本与画面创作提供可靠基础。 - ✅ **剧本生成** 基于选定事件和章节，系统自动生成结构化剧本，涵盖对白、场景描述、剧情走向，实现从文学文本到影视剧本的高效转换。 - ✅ **分镜制作** 根据剧本内容，智能生成分镜提示词与画面设计，细化前中后景、角色动态、道具设定和场景布局，自动根据剧本生成分镜，为视频制作提供完整路线蓝图。 - ✅ **视频合成** 集成 AI 图像与视频技术，可使用 AI 生成视频片段。整合在线编辑，支持个性化调整输出，让影视创作高效协同、快捷落地。 📦 应用场景 - 短视频内容创作 - 小说影视化实验 - AI 文学改编工具 - 剧本开发与快速原型 - 视频素材生成 🔰 使用指南

Existing repository AGENTS: `yes`
