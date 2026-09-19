# motor-muscle-sim Agent MEMORY

> Generated from the GitHub workspace catalog and repository-root documentation. Product source remains authoritative.

- Catalog: `motor-muscle-sim`
- Ownership: `owned`; write policy: `github-pr`
- Repository: `https://github.com/re8ch/motor-muscle-sim.git` (`main`)
- Catalog source: `catalogs/motor-muscle-sim/modules/motor-muscle-sim/source` (submodule)
- Metadata policy: `in-repository`
- Project type: `simulation`; lifecycle: `active`
- Knowledge: `README.md`
- Tools: `python`, `mujoco`
- Deployments: none declared
- Dependencies: none

## Working rules

Read the repository-root AGENTS.md first when present, then README and declared knowledge. For every `github-pr` module, start changes in a task-specific Git worktree based on the authoritative GitHub default branch; never develop in the default checkout. Preserve dirty checkouts before cleanup and remove clean task worktrees after merge or intentional closure. The `/Volumes/A1/service-backend` checkout is a physically read-only `github/main` anchor; only a clean fast-forward sync may temporarily unlock it. Use the module write policy; collaboration/read-only modules must not be pushed as owned code. Never copy credentials into patches, logs, MEMORY, or Git.

## Catalog and platform context

- Catalog index: catalogs/motor-muscle-sim/knowledge/resources.yaml
- Catalog overview: Motor Muscle Simulator
- Shared backend context: RE8CH 后台与集群上下文 本文件是所有 workspace catalog 模块共享的后台背景入口。产品源码仍以各自 GitHub repository 为准；集群、工作区锁定版本、工具与部署关系以本 service-backend 控制面仓库为准。 权威边界 - catalogs/<catalog>/catalog.yaml：项目组身份、所有权与知识入口。 - catalogs/<catalog>/modules/<module>/module.yaml：GitHub repo、默认分支、模块类型、 本机/R640 路径、工具、依赖和部署目标。 - catalogs/<catalog>/modules/<module>/MEMORY.md：注入隔离工作区的 Agent 背景； 它是生成文件，不替代产品 repository 根目录的 AGENTS.md。 - workspace/workspace.lock.yaml：R640 物化任务工作区必须使用的锁定 commit。 - indexes/*.json：跨 catalog 的项目、知识、工具与部署反向索引。 - inventory/：节点和基础设施清单；不得把清单地址当成凭据或直接写入目标。 - plugins/：PluginDefinition 与平台工具发布声明。 - operations/：workspace controller、Cloud Platform 等后台控制面实现与 runbook。 集群声明在哪里 - 集群 desired state 的主要产品仓库是 catalogs/platform/modules/cloud-functions/module.yaml 指向的 re8ch/cloud-functions，其中 manifests/k3s/ 按认证、网络、存储、可观测性、 AI operations、区域路由等域组织。 - GitOps、workspace catalog、锁定 revision、基础设施 inventory 和工具发布声明由 catalogs/platform/modules/service-backend/module.yaml 指向的本仓库维护。

## Repository synopsis

微电机人形机器人与复合肌体仿真器 这个原型模拟“数千到上万个 mm 尺度微电机单元驱动的人形机器人”，并保留一个平面复合肌体作为对照实验台。它不是把每个电机当成孤立伺服器，而是把高密度电机阵列抽象为分布式肌群：低维顶层运动命令生成关节目标，自适应控制器根据误差、热状态和保护状态调整增益，再把驱动展开到附着在 206 块骨头上的 motor pixel。 打开 `http://127.0.0.1:4173`。 MuJoCo 研究后端 `research/` 包含独立的 Python 3.11/MuJoCo 验证环境：19 个动力学刚体、 16 个受控关节和最多 20,000 个具有独立电流、温度、制造偏差与故障状态的 微电机。它提供教师数据生成、连续神经修正策略训练、三控制器对照实验、报告 和视频输出。安装及完整实验命令见 [`research/README.md`](research/README.md)。 建模层次 - **运动模式层**：行波步态、下蹲卷曲、双臂抓取、躯干扭转、蠕动步态、局部平衡修正。 - **206 骨骼图谱层**：颅骨、听小骨、舌骨、椎骨、肋骨、胸骨、肩带、上肢、骨盆、下肢、手骨和足骨全部进入 `ANATOMY_206`，启动时硬性校验数量。 - **人形骨架层**：脊柱、颈部、肩、肘、髋、膝、踝关节有角度、角速度、惯量、阻尼、限位和被动刚度。 - **分布式肌群层**：头、躯干、骨盆、上臂、前臂、手、大腿、小腿、足部分别铺设二维微电机阵列。 - **骨级执行器附着层**：每个 motor pixel 有 `boneIndex`，每块骨头都有非零 `boneMotorCounts`，浏览器指标显示 `206/206`。 - **自适应顶层控制层**：顶层模式只表达“走、卷曲、抓取、扭转、局部平衡”等意图，控制器通过 `adaptiveGain` 和 `adaptiveBias` 把误差反馈转换为局部执行器驱动。 - **神经场层**：每个单元有激活状态，支持局部波动、关节反射反馈和热抑制。 - **微电机层**：每个单元有电流、电压、电阻、力矩增益、制造偏差、反电动势和焦耳热。 - **复合肌体平面对照层**：高度场通过拉普拉斯弹性耦合，模拟柔性基体中的相邻力传递。

Existing repository AGENTS: `no`
