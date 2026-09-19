# ledger Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `ledger`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/ledger.git` (`main`)
- Catalog source: `catalogs/ledger/modules/ledger/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `finance-platform`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `ledger-plugin`, `docker`, `kubectl`
- Deployments: `ledger`
- Dependencies: `ledger-dashboard`

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/ledger/knowledge/resources.yaml
- Catalog overview: Ledger RE8CH financial product, dashboard and public website.
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

ledger 面向中国小企业一般纳税人口径的云端账本数据库、GnuCash 账务、税务底稿和 PostgreSQL 多租户辅助工具。 本项目的目标是让客户可以方便地使用云端账本数据库：客户通过用户体系登录，申请创建账本；系统在 `ledger` 数据库中为该账本创建独立 schema，载入 GnuCash 官方 PostgreSQL 表结构，并创建对应的访问角色。`docs/` 目录记录数据标准化和数据库操作手册，代码实现应优先遵循这些文档中的流程。 云端账本模型 | 层级 | 设计 | | --- | --- | | 用户表 | 暂定在 `ledger.public` 中保存微信用户表，用于识别客户和发起账本创建申请。 | | 账本申请 | 用户申请创建账本后，系统生成租户标识、账本 schema 名、桌面/应用访问角色。 | | 账本隔离 | 每个账本使用独立 PostgreSQL schema，避免不同客户账务数据混放。 | | 账本初始化 | 在新 schema 中载入 GnuCash 官方 PostgreSQL 表结构和科目模板。 | | 权限控制 | 为新账本创建独立角色，只授予目标 schema 的必要读写权限。 | | 管理视图 | `ledger.public.admin_allowlist` 记录可查看全部账本的微信 `openid`，管理员仍通过 JWT + RPC 访问，不直接暴露租户表。 | | 标准化处理 | 银行流水、GnuCash split、税务底稿等数据先按 `docs/` 中的标准格式整理，再进入账本或报表流程。 | 项目结构 | 路径 | 内容 | | --- | --- | | `scripts/` | 可复用脚本，包括 GnuCash 导出、税务底稿生成、PostgreSQL 租户导入。 | | `docs/` | 操作文档和流程手册，重点记录 GnuCash、税务底稿、PostgreSQL 多租户的安全操作方式。 | | `examples/` | 可公开的示例输入和样例输出，用来验证脚本格式和工作流。 | | `functions/` | Nuclio 云函数代码，用于微信登录、手机号换取和 PostgREST JWT 签发。 |

Existing repository AGENTS: `no`
