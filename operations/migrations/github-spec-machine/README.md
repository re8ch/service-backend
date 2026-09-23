# GitHub spec machine：外部对账契约

状态：设计与验证阶段；尚未启用。目标是逐步撤出集群内 Flux 对账职责，不能把 shadow 成功宣称为迁移完成。

## 目标与权威

用户通过 GitHub PR、声明文件、Checks、Environment 和 Deployment 操作；无需编写服务专用控制器。平台使用有限的 Actions 任务和轻量集群桥接，不新增外部常驻 GitHub App 服务、数据库或调度控制面。一个 RE8CH GitOps App 提供受限身份，App 身份不意味着必须部署 App 服务器。有限的通用适配程序不应被伪称为完全零代码。

GitHub main 是期望状态，签名提交是持久状态转换，PR 是提案与证据入口。Deployment 记录执行尝试和已接受修订，不成为第二份期望状态。Checks、Actions 日志和 artifacts 有保留期，长期验收记录必须有签名 Git 记录或不可变 release evidence；不在 Git 内保存原始遥测或明文 Secret。

目标集群不拉取 Git、不选择修订、不定时应用配置、不自行纠漂。Kubernetes 自身维持 Pod/Deployment 等运行语义的控制器继续工作；这里撤出的是 GitOps 配置对账。桥接只观察事实、传送事件和执行外部批准的有限操作。

## 四项基础保证

1. 严格基线：验证绑定 base SHA、候选 SHA/tree、策略版本、服务输入摘要和流水线版本。主线变化后旧接纳许可作废，作者主动更新提案。系统不自动 merge、rebase 或解决冲突。前序修订被撤销认可后，后继候选均失效，后继已执行修订标记 tainted，保留历史并停止新执行；恢复通过明确引用失效链的新 PR。不能自动恢复历史上已经发生的运行副作用。
2. 服务作用域：服务声明拥有的路径及渲染后 GVK/namespace/name/字段、依赖契约、订阅事件、核验周期、测试注册、并发/超时/资源预算。核验周期只触发读与比较，差异才产生执行请求。模块变更只运行拥有者与真实契约消费者的测试；重用证据必须匹配全部输入摘要。共享安全策略变更才扩展影响范围。
3. PR 对账：平台强制权限/基线检查加服务注册的 SHA 固定流水线。候选 PR 不能用自己修改的策略或流水线给自己授权；新注册先经独立平台审查。漂移恢复关联原批准 PR；改变期望配置必须有新 PR。PR 内容不能作为 shell 命令执行。
4. 越权拒绝：平台验证渲染后资源所有权，执行端 RBAC/admission 再次强制。核心网络契约不因节点不可用而修改。节点不符合契约时拒绝承载或报告降级；自动恢复不能更换出口、放宽规则或修改服务来迁就节点。

## GitHub 接纳协议的可行性门槛

不能把绿色 Merge 按钮、Actions concurrency 或 REST ref 的 force=false 直接当作严格事务。

- GitHub REST ref 更新没有 expected-old-SHA 参数；fast-forward 不是完整的比较交换。
- GraphQL createCommitOnBranch 有 expectedHeadOid，但会创建新提交；不能把它冒充接受原来经过检查的 SHA。
- Actions concurrency 可限制同时执行，但工作流开始顺序不等于作者提交顺序，也不能替代持久账本和授权锁。
- 当前 main 要求 PR 且允许 squash/rebase；严格接纳与这些规则的交互必须在隔离测试仓库证明，不能为试验先放宽生产规则。

优先验证精确 signed candidate 的单父提交接纳：候选只有一个父提交，必须等于期望 main；使用 Git receive-pack 的显式旧新 OID 更新，证明并发更新时只接受一个候选，且不生成合并提交。若 GitHub Rulesets 不允许在不破坏 PR 必经要求的前提下执行，报告能力缺口，禁止暗中降级为自动 squash。测试其他协议必须保持等价 tree 验证、签名、父版本绑定和 PR 审计。

配置接受的原子性仅指一个 Git ref 的转换。跨 Kubernetes 对象没有事务原子性；记录逐对象结果和 partial 状态。要求运行态原子切换的服务必须声明蓝绿/路由切换机制，未具备时拒绝这种保证。

### 本地协议实验（不等于 GitHub 已通过）

`tests/test_github_spec_acceptance_protocol.py` 使用临时 bare Git 仓库验证实际 ref 行为，未写生产 ref。五个实验确认：同旧 OID 的并发候选仅一个成功；即使新目标可 fast-forward，旧 OID 不符也被拒绝；CAS 本身不验证父关系；单独读取另一 validity ref 会与撤销竞争；本地 Git 多 ref transaction 可同时验证 validity 并更新 main。

GitHub API 是否提供同等多 ref 原子验证尚未证明，不能由本地 transaction 推断。生产默认设计因此将失效记录作为同一 main 账本的签名状态转换，接纳和失效经过同一个受信执行入口；Issue/Check 只作为投影。失效提交前，执行许可尚未被原子撤销，必须显式暴露这个时间窗口。失效提交推进 main 后，旧 base 的候选必须拒绝。任何替代协议须证明同样的竞态行为；不能把先读取 Issue 再更新 main 当事务。

这些测试尚未覆盖远端 receive-pack、GitHub Rulesets、签名校验、App token、PR 关联或执行端租约。下一个门槛是在隔离 GitHub 目标验证这些接口，生产 main 不作协议试验场。

补充实验通过真实本地 receive-pack 验证显式 `--force-with-lease=refs/heads/main:<base>` 拒绝第二个过期提案。该选项自身允许非 fast-forward，因此必须独立验证候选单父关系，并由服务端禁止非 fast-forward。测试已证明开启服务端限制后无关根提交被拒绝。累计七项本地实验通过。

2026-09-24 获准创建的私有 `re8ch/gitops-spec-lab` 已实测 GitHub 托管精确 SHA 接纳、required PR、指定 App 检查来源和并发单赢家；组织 required workflow 固定 SHA 也已证明不会被候选同路径修改替换。详见 [逐项远端证据](github-lab.md)。这些是个人接纳身份、零人工审批的隔离实验，不覆盖专用 App 接纳、生产规则兼容性或执行端租约，不得扩张为生产授权。

2026-09-23 通过组织 installations API 读取的权限确认：现有 `re8ch-flux-reaction`（4927936）具有 Checks/Deployments/Issues/PR/Statuses 写权限，但 Contents 只读；`re8ch-domain-promoter`（4950282）具有 Contents/PR 写权限，但不承担 Deployment/Checks 写入。目前未发现专用于本方案的 App，不能在运行时偷偷借用管理员个人令牌或合并多个 App 的权限来声称单 App 验证完成。优先演进现有 reaction 身份或注册一个受限身份，安装权限变更与 Rulesets 配置需单独审查。

同次读取 main Ruleset 23148035 确认 required PR、签名、线性历史和禁止非 fast-forward 都生效，allowed_merge_methods 为 squash/rebase；唯一列出的 bypass 是 Team 的 pull_request 模式。接纳协议不应假设直接 push 已获许可。组织仓库查询未发现明确的协议 sandbox，两个 archive 仓库不作为实验目标。

## 外部执行与观察

### 路径权限准入（未启用）

`scope_gate.py` 是有限的通用纯数据判定器，不执行候选、不访问集群、不授予部署权限。策略格式为 version、protected 路径前缀及 services 的 actorIds/paths；身份使用 API 数字 ID。保护路径优先于 grant；服务间重叠授权、未知服务/身份、跨边界改动、重命名来源越权、缺页/重复/不支持的差异均默认拒绝。`tests/test_github_spec_scope.py` 包含对抗测试。

接入固定验证器之前必须完成以下输入契约，当前未声称已完成：

- 策略及判定器从独立可信 SHA 加载，绝不能从候选内容选取。新增服务注册走平台审查，不能服务自授权。
- 通过 GitHub API 取得作者 ID、当前 PR HEAD/base 和全部差异；分页前后绑定同一 HEAD/base，发生变化即重试或拒绝。保守拒绝 3000 文件及以上，避免 PR files API 上限产生截断授权；总数来自 API，不来自 PR 声明。
- `pathEligible` 只是一项合取条件，不是执行许可。GitHub 验签、精确父关系、前序失效、固定验证器检查及原子接纳仍独立检查。
- 路径归属不证明对象归属：可信渲染还必须拒绝跨 namespace/资源身份、集群级对象、危险引用、符号链接和未固定远程 bases。渲染器本身应隔离执行且无部署凭证。此模块不解析 manifest，也不能代替该层。

远端现有 `operations/github-policy-reviewer/README.md` 经 API 核对，描述的是作者/签名/CI 准入，不是资源所有权策略；不复用它的成功状态来声称服务权限已校验。当前没有为生产臆造服务授权映射，lab 示例 actorIds 不代表生产权限。

显式保存 desired SHA/digest、accepted deployment revision、applied inventory digest 和 observed runtime generation/status，四者不得混同。每次尝试关联 PR、commit、Deployment、run ID、策略版本、资源集合和到期授权。

GitHub 托管 Actions 负责验证、计划、批准后的有限执行、健康评估及重试。每服务独立 concurrency group；失效链更新与接纳使用统一串行入口并在执行前复核。所有任务有 deadline、重试预算与终态；崩溃后先观察实际结果再重试，不能因没有回执就假定未应用。

集群观察器对受管对象使用 list/watch；保存游标，410 后 relist，删除事件和断线重连也产生失效提示。观察器不比较 desired、不选修订、不执行恢复。事件先持久化为 GitHub 事件记录并去重，再触发 Actions；GitHub 不可用时允许有限本地传输缓冲，但它不是期望状态。积压溢出标记 observation-gap，恢复后重新取快照。低频只读补偿允许；周期性全量 apply 禁止。

执行端只接受经过认证、未过期、匹配服务/资源范围/修订的操作。使用稳定的每服务 SSA field manager，不为每次执行创建新 manager。不允许通用 shell、长期 cluster-admin 或从待部署 PR 加载特权执行代码。OIDC broker 的 issuer/audience/workflow/environment 信任契约尚需验证；仓库历史记录曾将其标为 deferred，不能假设已部署。

授权撤销与网络分区无法保证即时终止正在进行的多对象操作：采用短期租约、每批复核和明确的部分执行记录。网络/API 不通时报告 unavailable，恢复后由 GitHub 重新批准执行。宿主机恢复不属于服务 actuator 权限。

## 迁移与验收

先验证接纳协议与授权，随后建立只读 shadow。暂选现有 domain-platform-adapter，仅作无写入观察；远端 chart 声明包含 LiteLLM/Nuclio 路由和集群级读取，尚不能认定它非关键。live inventory/业务影响确认不满足条件时必须重选，不能直接切换。它的外层包含 HelmRelease/OCIRepository，外层 parity 不证明 Helm controller 已被替代。完整撤出还必须覆盖该服务的 Helm 生命周期、artifact 获取和运行资源所有权。固定远端证据、四态观测及量化验收见 [shadow contract](shadow-contract.md)。

shadow 期间 Flux 保持唯一 writer。写入切换必须通过独立签名 PR：停止旧 writer、确认停止、转移稳定 inventory/字段所有权、启用外部 executor；不得同时写。逐服务迁移，最后处理 bootstrap、网络和根对账，完成前不删除 Flux。

验收包含：两个同基线 PR 竞争只接纳一个；基线前进使旧许可失效；前序失效禁止后继执行；无关服务不重跑；修改检查器不能自批准；渲染后跨服务资源拒绝；核心网络修改拒绝；status-only 不触发 apply；owned-spec drift/删除触发外部处理；重复、丢失和乱序事件；watch 410/relist；GitHub outage；API 分区；runner 被驱逐；执行后回执丢失；部分 apply；恢复后不执行过期修订。

必须重放历史 #535/#562/#563/#566/#573/#574 的故障类别：出口失效、节点迁移、跨节点 artifact 不可达和现场修复被覆盖。验收证明 GitHub 仍可记录、验证与组织恢复；API 不可达时准确报告不可执行，不能声称仍可部署。

最终完成条件是所有纳入服务由 GitHub 发起对账，集群无自行纠漂的 Flux 路径，故障注入和权限拒绝经过实际验证，长期账本可恢复。架构文档、绿灯单测、shadow parity 都只是中间证据。

## 证据

- 仓库基线：84fe99054d96f1fe216806bcfb155adb879ae059；已存在按 domain 分流 CI，但不等于按服务完全隔离。
- https://github.com/re8ch/service-backend/pull/573
- https://github.com/re8ch/service-backend/pull/574
- https://docs.github.com/en/rest/git/refs
- https://docs.github.com/en/graphql/reference/commits
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
- https://docs.github.com/en/rest/deployments/deployments

能力核对日期：2026-09-23。下一实现门槛是隔离验证严格接纳、App 权限和桥接身份，而不是提前启用 production apply。
