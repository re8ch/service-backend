# RE8CH self-service 管理面接手 Runbook

交接日期：2026-10-04，Asia/Shanghai。仓库基线：`ece9112dda4a6fc6736c09881b3d40abd1d406cd`。
本文是工作现场快照，不代表阅读时的实时集群状态。操作前必须重新发现工具、核对部署版本和资源 UID。

## 1. 接手目标与计数口径

用户要求：以管理员身份，通过 RE8CH self-service 操作 Headlamp 可见的全部服务；目录注册的功能必须实际可用，并有标准 API 测试。CLI 是 API 包装，退出码为零不是验收证据。管理面入口为 https://tools.re8ch.com ，不是另建门户。

**683 是未实现的操作条目，不是 683 个插件，也不全是写操作。** 当前已采集清单包含 10 个模块、893 条操作；210 条登记为 deployed，683 条 not_implemented。目录有 212 个工具定义：197 个 Kubernetes 读取工具、13 个 Mihomo 读取工具及 2 个发现工具。平台基础工具不包含在这 212 个数里。

| 模块 | 已登记 deployed 操作 | 待处理操作 |
|---|---:|---:|
| AdvFab | 30 | 68 |
| AdvFab legacy | 30 | 68 |
| Anatomy | 28 | 49 |
| Cilium | 0 | 275 |
| Mihomos cluster | 13 | 27 |
| Mobile Egress | 3 | 7 |
| Object Bucket | 7 | 14 |
| Public Edge | 3 | 7 |
| Rook Ceph | 76 | 133 |
| Rook Ceph CSI | 20 | 35 |
| 合计 | 210 | 683 |

逐条清单见 [CSV](unimplemented-operations-20261004.csv)，可按 plugin 分派同事。唯一定位键采用 `(groupVersion, method, path)`，operationId 作为附加绑定；部分源路由没有 operationId，不要编造原生 ID。CSV 是基线快照，实施时与当前 registry 差分，不把旧数字固化成目标。

本清单仍不是全覆盖：最近原生发现有 98 个 group/version，仅捕获 10 个，88 个未捕获；其他集群的授权与覆盖未证明。完成 683 条不等于完成全部 Headlamp 服务。

## 2. 已接入系统与能力深度

self-service 的管理员路径是 `platform_tool_catalog → platform_tool_call`，按返回的 backend/name/inputSchema 调用；不是绕过 self-service 直连另一个管理入口。

| 入口／系统 | 当前实现范围 | 不能据此推断的能力 |
|---|---|---|
| native / cloud-platform | Kubernetes、事件、节点、SSH、workspace、插件 OpenAPI／能力表／读取／HTTP checker | 任意 kubectl 可执行不等于每个服务有标准自助写接口 |
| cloud | Nuclio、LiteLLM、Fleet、Supabase、Neo4j、身份、Agent Runtime、可观测性、Harbor、数据库路由等状态与诊断 | 多数系统尚无完整业务管理 API |
| infra | 白名单 Deployment 重启／缩放、Ceph 状态与特定 OSD 恢复 | 任意服务或整个 Ceph 的通用写权限 |
| admin / tenant_admin | 用户、命名空间授权、租户与 Grant、数据库／Harbor 准备、BYOC 审批、动态工作流审核 | Dex、Pigsty 等全部原生操作已经接齐 |
| 租户原生工具 | 服务实例计划与生命周期、绑定、Claim、用量、命名空间、租户 Kubernetes、BYOC | 所有服务类、集群和用户均已端到端验收 |
| Harbor / Grafana | 制品、扫描、SBOM、标签、构建；数据源、查询、分析、渲染、探索管理 | 每个已注册操作均已线上验收 |
| 10 个插件模块 | 上表的专用读取与目录清单 | 未实现写入、语义或 UI 验收已经通过 |

声明覆盖报告里的“no_operation_adapter_in_this_registry”只描述插件操作目录，不代表整个 self-service 完全没有该系统入口。例如 admin/tenant 已有平台模块入口。不要把两个覆盖口径混在一起。

## 3. 线上实测现场与证据

### 读取验收

统一执行器已合并：[PR #1372](https://github.com/re8ch/service-backend/pull/1372)，签名合并提交 `595177b21b0811d3596901ef440a109dc9c0e8ae`。标准用例来自 [PR #1371](https://github.com/re8ch/service-backend/pull/1371)。本地 7 个 Node 执行器测试、84 个 Python API/HTTP 测试通过，CI 也已通过。

55 个集合 GET 用例完成：原始批次 **52 passed / 3 failed / 0 blocked / 0 not_run**。两项额外复查通过，不能覆盖原始失败记录：

- `listNetworkingRe8chComV1alpha1AdvancedFabric`：原批次 tool_error；复查通过，审计 `op_fce1138debe14b66af2aab95ee50d47b`。
- `listNetworkingRe8chComV1alpha2NetworkPathAssessment`：原批次 tool_error；复查通过，审计 `op_26b9e909a1844410abae897e3f326101`。
- `listNetworkingRe8chComV1alpha2RouterOSNode`：HTTP 200，但 response_schema_conformance failed，审计 `op_b8a4479ef562454cb4a9c4d44e07773c`。
- `listNetworkingAdvfabOrgV1alpha1NetworkTradeoffEstimate`：执行器内首次瞬时失败，第二次通过；attempts 与 recoveredAfterRetry 已保留。

两个 tool_error 的原文未持久保存，不能事后断言根因是身份服务。执行器有意不保存原始错误以防泄露；后续应增加稳定、脱敏的错误码分类，不能把未知错误全部标为网络故障。

已准备的扩展输入覆盖 197 个 Kubernetes 读取工具：145 项有输入、52 项缺样本（2 个 namespaced list、50 个 named get）。缺少的列表是 `listNetworkingAdvfabOrgV1alpha1NamespacedTrafficPolicy` 和 `listObjectbucketIoV1alpha1NamespacedObjectBucketClaim`。这些准备记录不是测试通过记录。

### Anatomy 真实写入

已通过 native kubectl API 包装验证一个 RuntimeDiff 临时请求的创建、控制器 Ready、同 commit 空差异、重复创建 AlreadyExists、带 UID/resourceVersion 前置条件的元数据更新、删除与 NotFound 清理。

- namespace：`runtime-anatomy-canary`；测试对象：`accept-diff-20261004-7c9e2f01`。
- UID：`48147fbb-2192-45ad-8a69-771f26a447f5`；**已删除，不应重用这个名字作为新用例**。
- 两端 commit：`rabf33a8e0-a`；另有历史 Sealed commit `rabf33a8e0-b`，使用前重新核对。
- 创建审计：`op_dfb3d0cc9df6424ebab8d3706cac69f5`；Ready：`op_3da900504d3349f9914f2a8efded4edf`；清理确认：`op_cf8afda82e5a4c03a0b624a7a74bba33`。
- 控制器源码 SHA256 与审查源一致：`303f084cfea22b05f6aa30c1967a8bfcaf53700c0b33471c12338d7106717d03`；当次核对审计 `op_f9dab692395b4f68b2049a309d678c3f`。

这证明原生写入与一项控制器语义，不证明专用 self-service mutation adapter，也不证明写请求的 HTTP 状态码／响应头验收。未通过的功能不能借用这条证据。

### 本地证据位置

`/Volumes/A1/worktree-archives/plugin-http-conformance-1362/` 是本机原始证据目录，**同事机器不会自动拥有这些文件**。见 [snapshot-20261004.json](snapshot-20261004.json) 的文件路径、SHA256 与大小；通过团队批准的文件通道移交并核对哈希。Git 仅保存摘要和审计引用，不提交原始运行遥测、凭据或浏览器内容。

关键文件：`standard-api-run-20261004.json`、`standard-api-rechecks-20261004.json`、`anatomy-write-acceptance-20261004.json`、`implemented-read-http-plan.json`、`finite-read-fixtures.json`、`routeros-v1alpha2-contract-gap.json`。归档还包含发布日志、固定镜像产物及可验证 Git bundles。

## 4. 部署、故障与恢复

最后一次实测 native image：
`registry.re8ch.com/functions-shared/re8ch-cloud-platform@sha256:41841bac44d7ef73adae77e2f7af7884337f9b4bd771f42a684d83b6ca2bae6d`。

Deployment：`ai-operations/re8ch-cloud-platform`，2/2 Ready，`maxSurge=1,maxUnavailable=0`；确认审计 `op_8c72533e63f14b1cbeb673fc00769887`。这是历史观测，接手后重查。

发布链：#1362 checker 实现 → #1366 上传超时修复 → 成功镜像发布 run `37181687695`（源 `d5b8c745aa5c258f67eb7f506c2751fd3064512f`）→ #1367 固定镜像与 16Mi 内存 /tmp → #1368 OCI 晋级 → #1369 更新策略修复 → #1370 晋级。

曾出现约 3 分 48 秒镜像拉取期间两个旧副本退出，native Service connection refused。拉取事件审计 `op_b90a9d84ef6f45fd9dc64e1a0b77c82b`。并非已证明程序崩溃；新 Pod 启动后恢复。#1369 已把 maxUnavailable 从 2 改为 0。不要回退此配置来“加速发布”。

另有间歇 `503 identity_unavailable`，与上述故障不能合并归因。一次 cloud 身份探针对 `dex.dex.svc.cluster.local` 的 DNS 查询失败，而认证网关存活；不足以证明身份根因。交接基线上已有其他人的 #1373–#1375 auth gateway 发布变更，未在本任务复验，勿覆盖或声称已解决。

排障步骤：

1. self-service 调用 catalog，记录 unavailableBackends；健康模块仍可使用。
2. native 可用时查看 Deployment、带 label 的 Pod、对应 Pod events、Service/EndpointSlice；只查所需字段，不 dump Secret、环境或 kubeconfig。
3. 区分 identity 503、catalog 模块发现失败、Service connection refused、Pod 拉取／调度／启动失败。观察超时不等于后台操作失败。
4. 若平台诊断路径失效，用户已授权使用 A/B/Mubao 控制面 SSH 排障。先从当前 inventory/SSH 配置确认目标和控制面健康；不猜别名、不导出凭据、不绕过授权去借用其他用户连接。
5. 配置修复走专用工作树、签名提交、绿色 CI、PR 和 Flux；不因已获 SSH 授权而绕过 GitOps。仅修复有证据支持的故障，不重启整个控制面。

## 5. 同事如何实现 683 条缺口

按 CSV 分配模块，但先按 disposition 决定是否应提供写入口：

| disposition | 条数 | 实施规则 |
|---|---:|---|
| authority_review_required | 420 | 先查控制器、源 API 和 GitOps 所有权，再确定直接操作还是 PR 工作流 |
| controller_owned | 134 | `/status` 等状态由控制器写；对管理员提供观测或诊断，不生成任意状态写工具 |
| resource_read_review_required | 85 | 审查字段与敏感数据后实现读取投影，不能把源对象原样输出 |
| runtime_capture_review_required | 10 | 采集涉及 Job/PVC/快照或文本，设计容量、保留与清理 |
| runtime_request_review_required | 10 | 临时运行请求，必须有明确终态与清理 |
| runtime_branch_cas_review_required | 5 | 绑定 expectedHead 与实际控制器 CAS 语义 |
| runtime_restore_review_required | 5 | 独立恢复目标与摘要验证，禁止默认覆盖业务卷 |
| gitops_pr_required | 5 | 从 self-service 发起 PR 并跟踪到集群生效 |
| active_probe_review | 5 | 明确流量目标、请求上限与影响后实现探测 |
| read_adapter_required | 4 | 实现源 API 读取适配与标准测试 |

“处理完”包括明确不可直接执行、转为专用工作流或控制器所有权，不是机械地给每条路由开放 POST/PATCH/DELETE。禁止把任意 kubectl 包装成通用自助写工具。collection delete、status patch 和共享网络切换尤其不能批量开放。

每个操作提交必须包含：

- 固定后端与受审查目标，输入／输出契约和源 API 版本、operationId、Schema 哈希；无任意 URL／命令注入入口。
- 管理员授权与目标作用域、Secret／敏感响应投影；跨租户与非管理员负面测试。
- 计划、执行、状态查询，绑定目标 UID／版本；复用现有计划哈希和幂等接口，不建立另一套身份系统。
- HTTP 响应断言、实际控制器效果、失败与冲突、重复请求、响应丢失后的查询恢复、删除或回滚、清理确认。
- 工具注册、平台 allowlist、目录能力表、CI、签名镜像和 GitOps 发布。不能只改目录 metadata 宣称上线。

业务资源已获纳入验收，默认仅在线可逆变更。执行前固定对象、基线、影响、回滚与停止条件；破坏性删除、业务覆盖恢复、共享公网切换另排维护窗口。失败清理或不确定写入结果时停止后续写入，列出残留对象与 UID。

模块次序：Anatomy Diff/Checkout Plan/GC DryRun → Branch/Capture/独立 Restore/Analysis → Mihomo 测试流量 → AdvFab/Public Edge 测试声明和发布 → Rook/CSI/桶完整生命周期。Cilium 先从 85 条待审读取起步，其余操作按所有权拆分。

Anatomy 特别注意：Ready/Failed 等终态通常不会因修改 spec 再次执行；Branch Active 对象修改不代表推进；Checkout 只有精确 `mode=Plan` 不进入物化路径，其他值不得透传；GC 当前只 DryRun，deleted=[]，不能宣称支持真实垃圾回收删除。

R640 公网改动前必须读 `operations/network/manifests/network/public-mesh/inventory.json` 的 publicAuthority。权威为 `223.159.210.222 → RouterOS DNAT/eBGP → 10.251.0.4/Cilium`；`192.168.88.251` 是 fallback，`10.181.22.128` 是 ZeroTier/dummy identity。禁止临时路由绕过。

## 6. 代码地图与验证命令

所有路径相对 service-backend：

- `platform/apps/tenant-mcp/platform-modules.json`、`module_registry.go`、`platform_tools.go`：管理员模块配置、动态发现、授权代理；`tenant_tools.json`：租户工具定义。
- `operations/cloud-platform/server.py`、`plugin_capabilities.py`、`plugin-operation-tools.json`：原生工具、Schema 绑定、生成读取工具；`plugin_http_conformance.py` / `plugin_http_transport.py`：真实有限 GET HTTP 检查。
- `operations/plugin-capabilities/openapi/`、`native-operation-matrix.json`、`mihomos-native-surfaces.json`：原生契约；`generate.py`、`verify.py`、`registry.json` / `registry.runtime.json`：生成与发布。
- `operations/plugin-capabilities/api-acceptance/`：55 个 cases、raw-envelope verifier、认证主机注入式 runner.cjs；`stagehand/`：管理面目录测试。
- `platform/cloud-platform/runtime.yaml`：native 部署；`clusters/re8ch/domain-sources/platform-cloud-platform.yaml` 和 `releases/platform-cloud-platform.yaml`：OCI 晋级。
- `operations/cloud-platform/anatomy-operation-authority.json` 与 `clusters/re8ch/charts/runtime-anatomy/files/controller.py`：写操作语义审查。

开始工作先运行 doctor，并读取 `operations/workspace-controller/GITOPS-WORKTREE-POLICY.md`。`/Volumes/A1/service-backend` 是只读 main anchor；从当前 github/main 创建任务工作树，一事一分支。不要修改其他人的工作树；产品源在独立仓库，GitLab 为历史只读。

在任务工作树执行：

```sh
python operations/workspace-controller/workspace.py doctor
python operations/workspace-controller/workspace.py context --module service-backend
python -m pytest -q tests/test_plugin_api_acceptance.py tests/test_plugin_http_conformance.py
node --test operations/plugin-capabilities/api-acceptance/runner.test.cjs
python operations/plugin-capabilities/verify.py
git diff --check
```

Python 依赖包括 PyYAML、jsonschema、pytest、Schemathesis 4.29.1；本机已验证环境为 `/tmp/re8ch-cilium-checks/bin/python`，此临时环境不可当作同事机器的依赖。Node 使用支持 node:test 的版本，Stagehand 要求 >=22.18。需要重新生成时在工作树运行 generate.py，检查差异后再提交。

运行线上 runner：认证主机加载 `runAcceptance({cases,catalog,call,checkpoint})`，catalog/call 对接本次已登录 Re8ch MCP；checkpoint 每次写入脱敏报告，持久化失败就停止。无需也不得拷贝 token。runner 顺序执行有限 GET，最多额外两次瞬时错误重试；当前不执行写入。工具调用超时由主机约束，不能超时后与仍在运行的请求并发重试。

`verify_report.py` 接收原始 MCP envelope 数组；runner 输出是另一种 versioned attempts 报告，**不能直接互换**。后续统一两种报告接口时增加兼容测试。runner 当前错误分类粗、没有独立进程断点恢复、没有已完成的 mutation runner；这些是真实剩余工作。

## 7. 已知缺陷与下一步

1. **RouterOSNode v1alpha2 Schema 不匹配**：`r640-boundary` 现有 spec 缺 routerASN、routeDomains、peers、protectedPrefixes、publicVIPs、continuity。CRD conversion=None，v1alpha1 是 storage，v1alpha2 served。先确定版本转换或数据迁移权威，不填猜测网络参数、不放宽 validator。详见本地 contract-gap 文件。
2. **目录结果尚未同步**：generate.py 的能力行仍把 HTTP/semantic 设置为 not_run；deployment-evidence.json 仍含旧镜像历史快照。新线上报告尚未接入目录生成。做按操作+Schema+镜像绑定的证据导入，过期证据不能继承，保留单用例范围标签；不要把 54 个曾通过的读取用例写成完整 operation coverage。
3. **Stagehand 未运行**：当前进程 STAGEHAND_CDP_URL、STAGEHAND_MODEL、STAGEHAND_MODEL_API_KEY、RE8CH_DIRECTORY_DETAIL_URL 均未配置；不存在已确认的专用环境位置。使用专用本地会话，不取浏览器 Cookie。既有 runner 只验证目录导航和表格，不验证工具调用、写计划、失败恢复或后端效果，需要扩展。
4. **跨集群与 Headlamp 完整性未证明**：以原生 API discovery、Headlamp 可见资源、服务原生 API 三者对账，识别不在 893 条里的服务。不要只完成已有 CSV 后就宣布全覆盖。
5. **清理与工作树**：临时 RuntimeDiff 已确认删除；本次 GET 无新增资源。#1367/#1372 任务工作树已归档并删除。旧 #1369/#1371 工作树可能仍在 `/Volumes/A1/worktrees/cloud-platform-rollout-availability`、`plugin-api-acceptance-cases`，必须检查 dirty 状态、归档后再移除，不能盲删。

管理面目录：
https://tools.re8ch.com/catalog/com.re8ch/cluster-plugin-operations/0.1.0?registryName=approved

每批交付必须分别给出注册、部署可用、HTTP 合约、业务语义、UI 语义、清理状态。所需项全部通过且无残留才算该批完成。鉴权错误、无样本、缺配置、控制器不支持都应显式记录，不允许用空测试、CLI 成功或目录存在替代验收。
