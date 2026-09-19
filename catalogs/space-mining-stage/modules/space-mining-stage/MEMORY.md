# space-mining-stage Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `space-mining-stage`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/space-mining-stage.git` (`main`)
- Catalog source: `catalogs/space-mining-stage/modules/space-mining-stage/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `game`; lifecycle: `incubating`
- Knowledge: `README.md`
- Tools: `npm`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/space-mining-stage/knowledge/resources.yaml
- Catalog overview: Space Mining Stage
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

Space Mining Stage 以 NASA/JPL 实时数据驱动的沉浸式近地小行星三维探索器。页面使用真实轨道元素传播目标位置，并将实时接近目标与 Bennu、Itokawa、Eros、Vesta 四个精选形状模型合并展示。 界面复用本地 RE8CH Navigator 与 Footer 品牌组件，星图操作面板采用深色 Liquid Glass 视觉体系。`/mission.html` 提供独立的转移路线与任务经济情景测算。 打开 `http://localhost:4179`。 Verify Data and model sources - NASA/JPL CNEOS Close-Approach Data API：未来 180 天近地接近事件。 - NASA/JPL SBDB Query API：轨道六根数、直径、SMASSII/Tholen 谱型、反照率、自转周期和观测质量。 - NASA/JPL Horizons：精选任务飞行器背景状态；不可用时不影响主场景。 - NASA DONKI：太阳活动通告状态。 - NASA/JPL 3D Resources：Bennu、Itokawa、Eros、Vesta 形状模型。 实时目标没有权威形状模型时使用基于 designation 固定种子的程序化岩石。程序化外观只用于视觉辨识，不表示真实地貌、光谱或矿物组成。天体尺寸和场景距离采用非线性展示比例。 资源色轴是由光谱分类生成的 0–100 代理指数，不是铁、金、稀土或水的实测质量百分比。任务经济页采用 SpaceX 2026 公开 Falcon 9 / Rideshare 价格规则；深空推进、捕获、返航、商品品位与公允价格均作为独立、可编辑的情景假设。 Main interface - `GET /api/nasa/mission-data`：实时目标、精选目标、轨道、模型、质量和来源状态。 - `GET /api/health`：服务健康状态。 - `/mission.html`：Earth → 小行星 → Earth 概念路线、Δv 代理值、过程成本与地面公允价值情景。 服务会缓存成功的 NASA/JPL 响应；单一数据源失败时使用已有缓存或精选目标降级，不阻塞页面进入三维场景。

Existing repository AGENTS: `no`
