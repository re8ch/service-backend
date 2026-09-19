# phonaid-service Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `phonaid`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/phonaid-service.git` (`main`)
- Catalog source: `catalogs/phonaid/modules/phonaid-service/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `service`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `docker`, `kubectl`
- Deployments: `phonaid`
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/phonaid/knowledge/resources.yaml
- Catalog overview: Phonaid Voice product services, clients and website.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

phonaid-service 独立的 AI 接听接线服务（FastAPI），用于和 Android 客户端或外部语音网关协同，实现呼叫会话编排、事件处理与 AI 策略下发。 - `app/main.py`：FastAPI 应用入口 - `app/api/v1/router.py`：REST 与 WebSocket 接口 - `app/core/config.py`：配置 - `app/core/security.py`：鉴权、签名校验、限流 - `app/models/schemas.py`：请求/响应/错误模型 - `app/services/session.py`：会话生命周期与状态机 - `app/services/orchestrator.py`：事件编排、AI 调用与动作生成 - `app/services/ai_client.py`：AI 客户端（当前默认 Mock） - `app/adapters/*`：语音接入抽象（含 Noop 适配器） - `app/routers/health.py`：健康检查 - `app/state/repository.py`：会话仓库（内存实现，默认可扩展） 快速启动 1. 安装依赖（推荐在虚拟环境内）： 2. 复制 `.env.example` 为 `.env`（可选）并启动服务： 3. 或使用 Docker： 启动后访问 `http://127.0.0.1:8080/ui/` 即可打开简易交互页面（含创建会话、事件、Action、WebSocket 流）。 - `GET /healthz` - `POST /api/v1/calls` - `POST /api/v1/calls/{session_id}/events` - `POST /api/v1/calls/{session_id}/action` - `WS /api/v1/calls/{session_id}/media` 新增：`WS /api/v1/calls/{session_id}/media` 支持连续音频流模拟。 发送示例（前端演示会发送此类 JSON 分块）： 服务端返回示例： 创建会话 上报事件 拉取/触发 AI 动作

Existing repository AGENTS: `no`
