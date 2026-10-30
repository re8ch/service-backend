# Stalwart 接入 Re8ch self-service

状态：实现与测试进行中；默认禁用，尚未发布。2026-10-30。

## 产品目标与入口

在现有 Re8ch self-service 中提供协作服务自助开通/绑定，以及邮件、
日历、轻量文件三个独立授权的工具组。AI 通过这些工具读取用户数据。
服务器管理通过同一 Re8ch 插件的受保护管理员目录提供。
不要求用户安装另一个 MCP 应用，不建立第二套注册、组织或身份系统。

用户流程：登录 Re8ch → 发现协作能力 → 选择开通或绑定已有账户 →
查看容量及权限计划 → 完成用户授权 → 获得不透明绑定 → AI 调用数据工具。
撤销绑定后，后续数据访问立即停止；撤销不会删除邮箱、日程或文件。

这里的“注册”是 self-service 的服务开通/绑定，不是允许匿名用户注册
任意 Stalwart OAuth 客户端、任意外部 MCP 服务或任意邮箱身份。

## 已核对的现有实现

- `platform/apps/tenant-mcp/` 是现有 Go MCP 服务，已有身份解析、服务实例、
  绑定、操作记录及工具遥测；复用这些模块。
- `main.go` 的 `/mcp` 与 `/self-service/mcp` 发布集合不同。后者当前只有
  17 个工具；不能以内部工具已注册作为公开 self-service 已可用的证据。
- `platform_tools.go` / `module_registry.go` 已提供平台管理员独立校验及
  后端白名单。平台管理工具沿用此目录。
- `service_tools.go` 的 live provisioner 白名单尚无 Stalwart；不能只添加
  catalog 声明就宣称协作服务可开通。
- `credentials.go` 的 broker 引用只支持 Harbor/Grafana，且按组织隔离。
  邮件、日历、文件凭据需要进一步绑定用户，不能直接扩大正则后复用组织凭据。
- `platform/stalwart/stalwart.yaml` 声明服务器 `v0.16.18`，
  `unified-identity.yaml` 配置了外部 OIDC 目录及 cluster-internal CLI。
  这是 Git 中的历史声明，不是本次已验证的运行版本。生产 Stalwart 已迁到
  `8iops/agent-gitops` 的 mail-system 权威；本目录不得作为第二个部署 writer。
  tenant-api 的实际部署权威是 `siren-observer/observability-gitops`。
- `bot-access-contract.yaml` 是现有端点/机器人访问约定；端点变更应修改它，
  不在新文件维护另一套端点清单。DAV 路径通过发现解析，不能继续假设邮箱
  local-part 就是服务端账户 ID。
- 本次 authenticated capabilities 返回 `tenantRef: re8ch`。受保护管理员
  目录可访问，但未发现 Stalwart 管理工具；普通 grants 也无协作服务。

## 工具边界

下列为目标接口；本地实现不等于当前插件已可调用。

| 入口 | 拟定工具 | 首版行为 |
| --- | --- | --- |
| self-service | `collaboration_connection` | 计划、连接状态、列出自己的绑定、撤销；返回现有操作和绑定引用 |
| self-service | `mail_query` | 列邮件夹、搜索及分页列邮件摘要 |
| self-service | `mail_read` | 读取指定邮件正文、附件元数据及受限附件内容 |
| self-service | `calendar_query` | 列日历、时间范围内日程、事件详情和可用性 |
| self-service | `calendar_respond` | 接受、拒绝、暂定一份已读取的邀请 |
| self-service | `files_list` | 列出授权文件集合和单层目录 |
| self-service | `files_read` | 读取轻量文件或取得短期受保护内容引用 |
| 管理员目录 | `stalwart.admin.read` | 读取允许的账户/域名元数据、配额和配置摘要 |
| 管理员目录 | `stalwart.admin.plan` | 生成允许的管理变更及 native validation 结果 |
| 管理员目录 | `stalwart.admin.apply` | 在已有审批/操作机制下执行准确计划并读回 |

邮件发送/删除、文件上传/删除、任意日历编辑不是首版默认权限。
后续分别增加显式写权限；不能把读工具升级成任意方法代理。
日历回复单列，因为它会改变状态并通知组织者。

底层协议：邮件用 JMAP；日历优先用运行版本已验证的 JMAP Calendars，
必要时实现 CalDAV Scheduling；文件优先用 WebDAV。
管理适配器调用官方 CLI 或等价管理 API，不开放 shell、CLI 参数字符串、
任意 JMAP method、任意 JSON patch 或任意目标 URL。

## 服务开通与绑定

复用 `service_catalog_list`、`service_instance_plan/create/get` 和
`service_binding_*` 的状态机；公开 self-service 只增加协作所需的受限入口，
不能因此暴露内部所有服务或管理员工具。

建议一个协作服务实例，共用一个 Stalwart 账户和配额，三个独立授权面。
避免分别申请邮件、日历、文件时重复创建三个身份。拟定服务类为
`collaboration.stalwart.v1`，但只有 provider reconcile、身份和端到端验证
完成后才能加入 live provisioner 白名单。

新账户开通必须由当前组织的 active membership、用户主体和平台控制的域名
分配规则决定。外部邮箱、别名字符串或同邮箱的 Admin 身份不能作为账户所有权
证明。已有邮箱通过用户授权绑定；账户冲突不自动接管、重置密码或变更归属。

计划返回账户来源（新建/已有）、存储容量、组织配额影响、保留策略、请求权限、
approval state、plan hash 和 idempotency key。重复请求不能重复开通账户。
准备好实际账户与授权绑定前，状态为 Pending/AuthorizationRequired，不能 Ready。

自助开通不得对每个用户新增人工 Git PR；Git 中审核服务类、额度、域名和
授权策略。策略内的实例请求由已有 provider controller 执行并留下操作记录。
服务器全局 desired configuration 仍走签名 PR 和现有部署链。

## 身份与授权

实际访问权限为：当前有效组织成员身份 ∩ 用户绑定 ∩ 用户批准范围 ∩
provider 原生 ACL。组织管理员身份本身不授予成员个人邮箱访问权。

绑定至少包含组织引用、经验证的用户 subject、provider account ID、
credentialRef、授权范围、允许的集合、generation、到期与撤销状态。
每次调用重新检查当前组织/主体和绑定有效性；共享 runtime class 不是所有权键。
共享邮箱、共享日历和共享文件还需原生 ACL；只凭输入路径不能访问另一个账户。

数据权限分别采用产品侧 `mail.read`、`calendar.read`、`calendar.respond`、
`files.read`。这些是 Re8ch 权限，不声称是 Stalwart OAuth 的原生 scope。
Stalwart 的服务 scope 可能比只读更宽，适配器必须继续限制具体方法。

优先复用受保护浏览器授权和 Credential Broker。provider OAuth token 不得
等同于 Re8ch 登录 token：现有 issuer/audience 合约不同，禁止无验证透传。
验证运行版本支持的 scope 后使用对应原生授权；若需应用密码兼容模式，
只能通过受保护用户输入页面进入 broker，不能进入 MCP 参数、聊天或日志。
UI 清楚显示它授权了哪个账户及哪些能力。

机器人默认使用单独的可撤销授权绑定。管理凭据只在管理适配器内使用，
现有 recovery administrator 不作为日常 AI 数据访问身份，也不交给客户端。
管理适配器逐项配置所需权限，不因某命令失败自动升级为全权限。

## 数据读取与日历回复

拟定首版上限（可通过现有服务计划审查调整）：每页最多 50 条；单次邮件正文
最多 128 KiB；附件/文件最多 5 MiB；直接返回文本最多 256 KiB；日历查询
最多 31 天，每页最多 100 个展开实例。响应携带截断和分页标志，不能静默漏项。
先检查元数据，再流式限量读取；声明大小不可信，实际字节数仍受限。

不自动爬取整个网盘，不递归同步，不自动解压压缩包，不渲染远程 HTML 资源。
二进制文件通过绑定所有者的短期资源引用传输，获取引用时再次验证权限。
邮件及文件内容是用户数据，不是工具调用指令；遥测不保存正文、附件、搜索词
或完整工具参数。审计记录主体引用、操作类别、资源引用、结果及字节数。

DAV/JMAP 发现结果只允许已审核 provider origin，重定向不携带凭据到新域名。
校验 DAV href、编码路径及响应大小；拒绝路径穿越、任意外链和嵌入凭据。

`calendar_respond` 只操作当前绑定用户对应的 attendee。输入使用已发现的
eventRef、预期事件版本及响应状态，不接受任意组织者或收件人。
先返回计划，执行时校验事件未变及仍有 RSVP 权限；记录幂等操作，未知结果
先读回，不能重试发送。创建一个普通 `.ics` 副本不等于接受邀请。
写入成功还要验证参会状态，以及原生调度是否已处理或仍在等待投递。

## 实施位置和验收

1. 验证运行中的 Stalwart 版本、管理 schema、JMAP session、OAuth scope、
   DAV principal/collections 和调度能力。逐能力返回 Available/Unsupported/
   AuthorizationRequired/ProviderUnavailable，不能以 TCP 连通代替可用性。
2. 在现有 provider controller、service catalog 和 grant 策略增加协作服务，
   完成用户级 broker 绑定及浏览器授权。沿用操作/幂等存储，不新建协调服务。
3. 在 `platform/apps/tenant-mcp/` 增加三个数据适配器及注册入口；同步
   `self_service_http.go` 能力发现、两条 MCP 发布路径、tool annotations、
   发布白名单、插件说明和 User Hub 所消费的同一后端契约。
4. 管理适配器通过现有 Cloud Platform/模块 registry 发布；现有 CLI
   dry-run 负责 native validation，平台计划负责权限、审批及审计。
   未审核的配置不能由 CLI apply 绕过 GitOps。
5. 隔离测试覆盖同组织不同用户、跨组织、Admin/应用组织混用、撤销、过期、
   未授权共享集合、超限内容、危险 href、provider 超时和错误脱敏。
6. 协议测试覆盖 JMAP 分页/方法错误、正文截断、附件下载；日历时区、循环
   事件、版本冲突、重复回复；DAV 部分失败和受限下载。
7. 用非生产测试账户完成“自助开通/绑定 → 读测试邮件 → 查测试日程 →
   显式回复测试邀请 → 读共享小文件 → 撤销后拒绝访问”的真实链路。
   管理工具须证明普通成员不可见也不可调用，审批计划不能跨主体复用。
8. GitHub checks、发布摘要及实际 MCP `tools/list` / `tools/call` 均验证后，
   才标记发布完成。设计文档、mock 测试或 merged PR 都不是上线证据。

现有原生能力已涵盖邮件/日历/文件存储及调度。需要新增的是 Re8ch 的用户级
授权绑定、受限协议适配和工具发布；不需要新邮件服务器、新文件存储、
自建日历调度器、轮询同步器或替代注册系统。

## 官方依据

- [Stalwart CLI](https://stalw.art/docs/management/cli/)
- [客户端发现](https://stalw.art/docs/server/clients/)
- [Calendar](https://stalw.art/docs/collaboration/calendar/)
- [Scheduling](https://stalw.art/docs/collaboration/scheduling/)
- [WebDAV](https://stalw.art/docs/http/webdav/)
- [OAuth scopes](https://stalw.art/docs/auth/oauth/scopes/)

以上为当前在线文档，不能替代 v0.16.18 运行实例的兼容性验证。

## 本次分工与交付状态

Project 目标归属：`8iops`，名称 `Re8ch Collaboration Self-service`。
Project 已创建：https://github.com/orgs/8iops/projects/1 。用户已明确授权本次
Project、Issue 和 PR 使用已登录的 Woomou CLI 身份；签名和仓库保护不变。
三个已有仓库足够，不创建重复源码仓库。

| Agent | 实现范围 | 仓库 | 状态 |
| --- | --- | --- | --- |
| admin_implementation | 受限管理读取、审核计划及执行 | re8ch/service-backend | 本地实现和测试完成 |
| collaboration_implementation | JMAP 邮件/日历、WebDAV 文件客户端 | re8ch/service-backend | 本地实现和协议测试 |
| root | 用户授权、绑定、工具注册及集成 | re8ch/service-backend | 集成与测试 |
| gitops_delivery | DAV 发现、OAuth 兼容门禁、发布契约 | 8iops/agent-gitops | 本地实现和检查完成 |

第一版连接现有用户账户，使用 Stalwart 原生 device authorization。工具
只返回 verification URI/user code；device code/access token 保存在现有
Credential Broker Secret，绑定按组织和用户双重检查。默认关闭，需显式
`collaboration.stalwart.v1` grant。当前不保存 refresh token：到期需重新授权。
不把连接功能声称为新邮箱开通；新账户 provisioning 仍需后续 provider 工作。

兼容门禁：官方 v0.16.18 OAuthClient schema 缺少新版 `scopes`/`firstParty`
字段。运行 schema、服务 scopes 和用户 session 验证通过之前不得启用。
禁止通过 first-party/unscoped token 降级绕过此门禁。需要独立审核升级或
确认实际运行版本已具备能力。
