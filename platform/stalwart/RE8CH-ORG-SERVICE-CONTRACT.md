# Stalwart 作为 Re8ch Org 基础服务

状态：目标产品契约。它扩展现有 `collaboration.stalwart.v1` 用户绑定，但不把 Re8ch
Admin 身份、恢复管理员或平台 Secret 暴露给租户。

## 所有权与产品边界

- 服务发布者和资源所有者是 **Re8ch** 组织 (`tenantRef: re8ch`)，不是 Re8ch Admin。
- Re8ch Admin 仅保留服务器、域名、证书、全局策略、灾备和平台凭据等基础设施管理。
- 账户实例属于 Re8ch Org 下的具体用户或显式共享主体；Org membership 本身不自动授予
  其他成员邮箱内容访问权。
- Personal Org 可以作为受支持目标，但必须创建独立实例/绑定，不能借用 Admin 邮箱。
- Stalwart 留在 Atlantic，通过公网 `id.re8ch.com`、`mail.re8ch.com` 提供服务；普通消费
  不依赖 AmongCluster。

## 服务模型

发布一个 live service class：`collaboration.stalwart.v1`。

一个 ServiceInstance 表示一个 Stalwart 账户及其邮件、日历、通讯录、文件和配额。
创建流程动态执行，不要求为每个账户修改 GitOps：

1. Re8ch Org active member 调用 `service_instance_plan`。
2. 计划解析账户地址策略、现有账户冲突、配额、保留期、计量影响和授权状态。
3. 使用稳定 idempotency key 创建实例；provider controller 调用受限管理适配器创建账户。
4. 用户通过 User Hub SSO 建立个人 `collaboration_connection`。
5. ServiceBinding 将账户实例与当前 subject 绑定；共享账户另走 Stalwart 原生 ACL。

创建账户与使用账户是两个独立授权面：创建需要服务实例权限；读取邮件、日历、联系人、
文件或执行用户操作需要用户自己的 connection 和 scope。

## CLI 能力

产品目标是覆盖 Stalwart CLI 的全部正式能力，但不能提供任意 shell 或未审计 argv
passthrough。每个 CLI 能力必须映射为类型化 action：

- Account、Domain、Group、List、Role、Principal 生命周期。
- 配额、别名、成员关系、权限和凭据轮换。
- 导入、导出、维护、队列、报表、配置查询及官方支持的运维动作。
- 邮件、日历、通讯录、文件等用户协议操作继续通过 JMAP/CalDAV/CardDAV/WebDAV 工具。

能力按风险分层：

| 层级 | 范围 | 授权 |
| --- | --- | --- |
| user | 本人账户数据和偏好 | 用户 connection + scope |
| org-operator | Re8ch Org 拥有的账户创建、停用、配额和共享关系 | Org operator/admin + plan/apply |
| platform | 域、服务器全局配置、证书、恢复与灾备 | Re8ch Admin 平台目录 |

所谓“CLI 一切功能”是覆盖全部功能语义，不是把 recovery token、CLI 字符串或任意 provider
origin 交给模型。所有写操作必须先计划，使用 plan hash、预期状态和幂等键；未知结果先读回。

## Bifurcate 用量事实

Bifurcate 记录中立、不可变的 UsageEvent；不保存价格，也不直接记账。Billing 按稳定 meter
映射价格。所有事件由验证后的 `tenantRef`、ServiceInstance UID、accountRef 和 operationRef
关联，禁止接受调用方自报租户。

首批 meter：

| meter | unit | 采集来源 |
| --- | --- | --- |
| `stalwart.account.active` | account-hour | provider controller 定期快照 |
| `stalwart.storage.logical` | byte-hour | Stalwart account quota/usage |
| `stalwart.mail.ingress` | message / byte | Stalwart metrics/投递事件 |
| `stalwart.mail.egress` | message / byte | Stalwart metrics/队列结果 |
| `stalwart.jmap.request` | request | tenant tool telemetry |
| `stalwart.calendar.operation` | operation | calendar tool/provider audit |
| `stalwart.contacts.operation` | operation | contacts tool/provider audit |
| `stalwart.files.transfer` | byte | DAV adapter bounded byte counter |
| `stalwart.admin.operation` | operation | 类型化 CLI 管理适配器 |

事件保持 `pricingApplied=false`、`accountingPosted=false`。正文、主题、收件人、联系人、日程内容、
文件名、token 和完整工具参数不得进入 UsageEvent。重复 provider 回调按稳定事件键去重；累计量
必须记录采样窗口和单调游标，避免重启后重复计费。

## 生命周期和保留

- Suspend：停止新登录和写入，保留数据并继续记录适用的存储 meter。
- Resume：恢复同一账户，不创建重复身份。
- Delete：软删除 ServiceInstance；账户进入可配置保留期，期满后才执行 provider 清理。
- Revoke binding：立即停止 AI 工具访问，但不删除账户和邮件。
- Org membership revoked：所有该 subject 的新调用失败；共享资源由原生 ACL 与组织策略处理。

## 发布与验收门槛

1. `service_catalog_list` 返回 live `collaboration.stalwart.v1`，不是 intent-only 声明。
2. 使用 Re8ch Org 普通测试用户完成创建、重复创建幂等、SSO 绑定、Mail/Calendar/Contacts 使用。
3. Admin 身份不能作为 Re8ch 账户所有权替代；跨用户、跨 Org connectionRef 必须拒绝。
4. 覆盖每个正式 CLI action 的 schema、权限、plan/apply、审计和 provider readback。
5. UsageEvent 对 active account、storage、协议请求和管理动作完成去重与租户归属验证。
6. Billing 只消费 meter；价格变化不重写 Bifurcate 历史事实。
7. 公网 Atlantic 故障、provider timeout、未知写结果和撤销后访问均有真实验收。
8. GitHub checks、Cloud SSA 发布、实际 provider 状态和插件 `tools/call` 均通过后才标记 Ready。

