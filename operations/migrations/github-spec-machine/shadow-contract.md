# Adapter shadow：证据、状态与观察边界

状态：设计中，未连接 live API，未部署 observer，Flux 仍是唯一 writer。
2026-09-24 经 GitHub API 固定读取 main SHA
`85fc14399322d70782d480b9fabad540cbb68de8`；不将本地 worktree 当作集群事实。

## 实际声明，而非假定能力

| 来源（固定提交下） | 核实的声明 | shadow 必须保留的语义 |
| --- | --- | --- |
| `clusters/re8ch/domain-sources/platform-adapter.yaml` | OCI digest `sha256:9c190d0a01f21569e6034aa091c97398cbf4b8cefc9675b29f0a634679c2e21a`，cosign OIDC issuer/subject，registry reader | 校验实际发布 artifact 的摘要与签名身份；从源码重新 render 不等于验证 artifact |
| 同文件 Kustomization | interval 10m、retryInterval 1m、timeout 5m、prune false、wait false、SOPS，未声明 dependsOn | 不把未检查健康解释成健康，不删除遗留对象，不绕过解密与失败重试 |
| `platform/adapter/release.yaml` | chart OCI digest `sha256:feb775e9995f8ba8f6d36b5bd0b095b8598346085b4536e8b8c2130246ec0870`，source interval 30m | bundle 与 chart 是两个独立 artifact/revision |
| 同文件 HelmRelease | interval 10m；install retries 3；upgrade retries 3、rollback strategy、cleanupOnFail；namespace/storageNamespace adapter | 外层 apply 不能替代 Helm 安装、修复、回滚与 release storage 语义 |
| `releases/platform-adapter.yaml` | DomainRelease bundle digest 与外层 OCI 引用相同 | 校验发布账本与 source 引用一致；不从 images=[] 推断没有运行镜像 |
| `operations/ci/domain-catalog.yaml` | adapter 与 connectool、hyperswitch、external-projects 同属 delivery 测试域 | 当前 CI 域不是服务权限边界；不能直接用域级 fan-out 实现服务隔离 |

声明来源：[固定提交](https://github.com/re8ch/service-backend/tree/85fc14399322d70782d480b9fabad540cbb68de8)。chart 源代码只是参考，未下载固定 OCI chart，不能证明二者字节相同。

## 候选风险纠正

adapter chart README/values 默认包括 LiteLLM OpenAI 路由及跨 namespace Nuclio discovery；rbac template 包括 ClusterRole/ClusterRoleBinding，读取 Services、EndpointSlices、AdvancedFabric 和 Nuclio 对象。publicEdge=false 不代表没有内部调用者，localCanary=true 不代表整个 release 是 canary。

因此仅选择它做**无写入 shadow 候选**，撤回“已证明非关键”的任何隐含判断。切换前必须通过授权 observe 接口确认调用流量、调用者、实际 Helm inventory 和业务 owner；不符合非关键条件则重新选择一个现有 Kustomization，而不是削减这个要求。当前 observe MCP 工具未在会话暴露，技能禁止绕行原始 kubeconfig/Secret；live 风险评估缺证据。

## 四种状态分别记账

每份报告显式携带 repository、service、cluster identity、policy SHA、render toolchain digest、采样起止时间及 evidence URLs：

1. `desired`：本次 API 读取的 main SHA、bundle/chart digest、服务输入摘要。main 再前进并不改写这次报告。
2. `accepted`：受信接纳账本 revision、Deployment ID、签名和准入证明；shadow 尚未创建这套真实接纳记录，必须为 unknown，不能拿 Flux lastAppliedRevision 冒充。
3. `applied`：live 对象 UID/generation、受管字段摘要、Flux lastAttemptedRevision/lastAppliedRevision、Helm release revision。保留各自原始 revision 格式，OCI digest 不伪装成 Git SHA。
4. `runtime`：observedGeneration、conditions、工作负载 ready 状态及错误时间；和 spec 摘要分开。status-only 更新可改变健康报告，但不产生 apply 意图。

另列 `fluxReference` 作为 shadow 的对照，不覆盖 accepted。缺失、过期、无权读取均标为 unknown/unavailable，禁止绿色默认值。跨 kind 的 list 不是全局原子快照，记录每个集合的游标/采样区间，变化窗口中报告 inconclusive，不能谎称一致时刻的全局 parity。

## 最小观察衔接协议

Observer 只负责指定对象的 list/watch、脱敏投影和传输，不持有 Git main 或修复逻辑，不写 Kubernetes，不触发本地 apply。

- 初始 list 后从该集合 resourceVersion 开始 watch；resourceVersion 是不透明游标，不跨资源做大小比较。410 时重新 list，记录 observation-gap 并重建快照。bookmark 只说明进度，不证明对象健康。依据 [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/)。
- 身份键包括 cluster/GVR/namespace/name/UID；同名删除重建不能被旧 UID 事件覆盖。缓存断线后不能继续报告当前健康。
- 传输字段仅限身份、游标、generation、显式许可的 spec 字段及 conditions；不发送 Secret、环境变量值、完整 ConfigMap 内容或未审查的错误文本。摘要也不用于公开暴露低熵秘密。
- 外部 GitHub 处理者将事件作为失效提示，重新确认 main/accepted 与当前观察再决定意图。重复事件允许，幂等键至少包含 service/accepted revision/object UID/observed owned-spec digest/policy revision；去重只是优化，不可作安全授权。
- 断网允许有限传输缓冲，溢出记录 gap；GitHub 恢复后先获完整快照再判断。可低频只读补偿，禁止定时全量 apply。
- Issue 保存批次/异常事件摘要和证据链接，不逐条写入所有 status 更新；Checks 展示投影。未投递事件必须可重试，不能把一次 repository_dispatch 当持久队列或 exactly-once。

不直接安装通用“全部资源 watch”权限。先以 PoC 的 Flux 对象与实际 release inventory 形成允许列表；如工具无法提供这些投影，新增 observer 的权限与安装属于下一次明确审查。

## Shadow 验收（目标，不是已达到）

- 至少 24 小时、覆盖至少 3 次既有 Flux 成功周期；每份结论有固定 desired/source/toolchain 身份。无不可解释的对象集合/受管字段差异。
- 分开验证：artifact 身份、外层对象渲染、applied 字段、runtime 健康；外层 parity 不计作 Helm parity。
- 隔离故障注入包含 spec 改动、status-only、删除重建、乱序/重复、410、断网、gap、main 推进。生产不做注入。status-only 的 apply 意图为 0，过期修订执行授权为 0，shadow 集群写入为 0。
- 健康连接下事件被 GitHub 接收的 p95 目标 60 秒；受 Actions 排队影响的报告延迟单独计量，目标 p95 5 分钟。不能保证时如实判失败，不改成周期重部署。
- 断网期间必须显示 unavailable；恢复完整快照前不得宣称收敛。明确统计漏报、误报、重复意图和最大 gap。
- 任何 production writer 切换前仍需非关键确认、专用 App 最小权限、执行授权/撤销与单 writer 交接验证。本文件不授予这些权限。
