# qianwen-web Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `qianwen`
- Ownership: `collaboration`; write policy: `authorized-upstream-pr`
- Repository: `https://github.com/OCGS-with-pattern/Qianwen-web.git` (`master`)
- Catalog source: `catalogs/qianwen/modules/qianwen-web/source` (submodule)
- Metadata policy: `control-plane-overlay`
- Project type: `web`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `npm`
- Deployments: none declared
- Dependencies: `qianwen-app`

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/qianwen/knowledge/resources.yaml
- Catalog overview: Qianwen Collaboration workspace. Writes require an authorized upstream or fork PR.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

**中文** | [English](./docs/README.en.md) 荆州A短剧工作流 Web **JingzhouAI 前端应用 ** **基于 Vue 3 + TypeScript + Vite 构建的现代化 Web 界面 ** **AI短剧工厂的用户操作端 🎨** > 当前生产 API 由主仓库 `nuclio/` 下的 Go / Nuclio 后端提供；历史 Node.js 后端已归档到 `deprecated/node-backend/`，不再作为正式联调或部署入口。 > 🎯 **现代化前端架构**：采用 Vue 3 组合式 API、TypeScript 类型安全、Vite 极速构建，打造流畅的用户体验！ 🌟 技术栈 - **框架**：Vue 3.5+ (组合式 API) - **构建工具**：Vite 5.4+ - **语言**：TypeScript 5.6+ - **状态管理**：Pinia 2.2+ (支持持久化) - **路由**：Vue Router 4.4+ - **UI 组件库**： - Ant Design Vue 4.2+ - Element Plus 2.13+ - VXE Table 4.17+ - **工具库**： - Axios - HTTP 请求 - VueUse - Vue 组合式工具集 - Day.js - 日期处理 - Mammoth - Word 文档解析 🎨 主要功能模块 荆州A短剧工作流 Web 提供了完整的短剧创作前端界面，包含以下核心模块： - ✅ **项目管理** 创建、编辑和管理短剧项目，支持项目状态追踪和多项目并行开发。 - ✅ **原始文本编辑** 导入和编辑小说原文，支持 Word 文档解析，智能文本清洗和章节分割。 - ✅ **角色素材库** 管理角色设定、角色图片等素材，支持批量生成、手动上传和在线编辑。 - ✅ **大纲管理** 可视化编辑故事大纲和事件线，支持拖拽排序和智能生成。 - ✅ **剧本编辑器** 结构化剧本编辑界面,支持对话、场景、情绪等多维度标注。 - ✅ **分镜设计** 可视化分镜画布，支持拖拽布局、图像检测和 AI 对话式分镜生成。 - ✅ **视频配置**

Existing repository AGENTS: `no`
