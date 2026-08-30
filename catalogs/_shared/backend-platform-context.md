# RE8CH 后台与集群上下文

本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub
repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend
控制面仓库为准。

## 权威边界

- catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。
- catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、
  本机/R640 路径、工具、依赖和部署目标。
- catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景；
  它是生成文件，不替代产品 repository 根目录的 AGENTS.md。
- workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。
- indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。
- inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。
- plugins/：PluginDefinition 与平台工具发布声明。
- operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。

## 集群声明在哪里

- 集群 desired state 的主要产品仓库是
  catalogs/platform/modules/cloud-functions/module.yaml 指向的
  re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、
  AI operations、区域路由等域组织。
- GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由
  catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。
- 网络控制面参考 advanced-fabric 与 mihomos-cluster；插件/MCP 分发参考
  codex-admin-plugins、tool-distribution 与 mcp-registry。
- 对集群做变更前，先读取目标 repository 的 AGENTS.md，观察线上状态，使用
  Cloud Platform MCP；正式 GitOps 变更需签名提交、CI、MR/PR 与 Flux 校验。

## 目录工作方式

1. 从当前模块 module.yaml 确认 repo、ownership、write policy 和 dependencies。
2. 阅读当前 catalog 的 knowledge/overview.md 与 knowledge/resources.yaml。
3. 产品功能改到对应 GitHub repo；跨项目背景、锁和平台声明改到本控制面仓库。
4. Mac mini 是首选构建机；R640 使用锁定 SHA 的任务 worktree，禁止移动
   workspace/modules/ 下的本机链接。
5. 凭据、解密后的 SOPS、runner token、kubeconfig 与本机 symlink 不得提交。

## 职责分类

- 前端：Web、Android、iOS、小程序、Dashboard。
- 后端：API、微服务、云函数、MCP 服务。
- 数据库/数据：数据库模型、数据管道、数据及资产仓库。
- 运维/基础设施：GitOps、Kubernetes、网络、插件分发和控制面。
- 共享核心/研究：仿真、机器人、核心实验及跨域模块。

Linear 项目应在摘要中显示上述分类，并在描述中列出每个 catalog module 对应的
GitHub repository；catalog/module.yaml 始终是 repo 映射的机器可读事实来源。
