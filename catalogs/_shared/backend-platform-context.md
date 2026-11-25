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

## 多集群声明与访问边界

- `re8ch` 主集群和 `re8ch-atlantic` 等子集群是独立 Kubernetes 权威。每个集群
  分别拥有 API 入口、CA、OIDC audience、GitHub Environment、RBAC、SSA 状态和
  故障域；ReleaseContract 的 `target.cluster` 必须选择对应执行 profile。
- 主集群 Cloud SSA 优先使用多入口 `kube-api.service.re8ch.com`；
  `kube-api.re8ch.com` 仅作临时回退。Atlantic 使用自己的 API 入口，不经主集群
  API，也不把主集群入口当成跨集群代理。
- AmongCluster 只交换有界的服务身份、端点、健康、容量与路径事实。它不分发
  Kubernetes 对象、kubeconfig、Secret 或集群管理员权限，也不替目标集群执行 SSA。
- 应用部署在实际承载它的目标集群。跨集群消费优先走服务的 PublicEdge 域名；
  只有服务契约明确要求时才使用 AmongCluster 的有界私网路径。内部调用同样默认
  使用 PublicEdge，以减少对集群内部拓扑的耦合。
- GitHub PR、签名制品和 bounded Cloud SSA 是正式写入路径。Kubernetes 是运行态
  权威；本地 Flux writer 已退役，保留的 Flux 声明仅作历史或迁移输入。
- 网络控制面参考 advanced-fabric、AmongCluster、PublicEdge 与 mihomos-cluster；
  插件/MCP 分发参考 codex-admin-plugins、tool-distribution 与 mcp-registry。

## 目录工作方式

1. 从当前模块 module.yaml 确认 repo、ownership、write policy 和 dependencies。
2. 阅读当前 catalog 的 knowledge/overview.md 与 knowledge/resources.yaml。
3. 产品功能改到对应 GitHub repo；跨项目背景、锁和平台声明改到本控制面仓库。
4. GitHub-hosted Enterprise runner 是首选 CI/构建执行器；Mac mini 处理本机挂载审计。
   R640 使用锁定 SHA 的任务 worktree，禁止移动
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

## Admin/BYOC 工具链恢复上下文

- 最新有界恢复记录及未完成验收：[Issue3002](https://github.com/re8ch/service-backend/issues/3002)。
- 可重复执行的验收步骤：[工具链运行验收](../../operations/releases/toolchain-runtime-verification.md)。
- 发布成功、旧副本 Ready、Connected Accounts、SDK capture ID 均不能单独作为恢复证明。
  同一 Admin 会话对 self/Atlantic 的读写、SSA、AF 观测和变更收敛需分别验收。
- AMD 故障先暂缓，优先跑通核心功能；DNS 服务恢复不等于该故障域已修复。
- 用户偏好架构完整的修复和充足的实验容量/时限，避免小配额反复阻断；
  实验仍须保留目标归属、凭据边界、审查和真实运行验收。
