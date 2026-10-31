# Stalwart 与 Re8ch 统一 OAuth 接入交接

状态：User Hub PKCE、refresh token 和 self-service 数据读取已于 2026-10-31
完成生产验收；`id.re8ch.com` 公网 Gateway 仍有独立的 503 故障。本文用于交接
统一 OAuth 第三方接口，不替代 Stalwart 原生 OAuth、设备授权或 JMAP/DAV 能力。

## 1. 目标和不变边界

目标是让登录 Re8ch User Hub 的用户或 Agent，在 Re8ch self-service 中授权其
已有 Stalwart 账户，并通过动态工具访问自己的邮件、日历、通讯录和轻量文件。
OAuth 是账户授权边界，不是第二套邮箱账户、组织或用户目录。

三类入口必须并存：

| 入口 | 职责 | 是否保留 |
| --- | --- | --- |
| `auth.re8ch.com` | User Hub 统一登录；Authorization Code + PKCE；第三方授权码、access token、refresh token 的签发和刷新 | 是，普通用户默认入口 |
| `id.re8ch.com` | Stalwart 的公开 JMAP、DAV、账户页面以及原生 OAuth/设备授权兼容入口 | 是，不得因 User Hub 接入而删除 |
| `stalwart.mail-system.svc.cluster.local:8080` | tenant-mcp 到 Stalwart 的集群内数据通道 | 是，仅供受控运行时使用，不是公开 issuer 或 redirect URI |

设备授权继续用于无浏览器 Agent、机器人和兼容场景。User Hub PKCE 是新增的统一
入口，不是废弃 Stalwart 原生授权的迁移开关。直接 JMAP 应用密码、Webmail SSO
和平台管理凭据也属于不同边界，不能互相替代。

## 2. 已部署架构

```text
用户/Agent
   │ Re8ch MCP 身份（组织 + subject）
   ▼
Re8ch tenant-mcp / self-service
   │ 1. authorization redirect
   ├──────────────────────────────► auth.re8ch.com
   │                                  │ User Hub session
   │ 2. code + PKCE                    │ signed OAuth tokens
   ◄───────────────────────────────────┘
   │
   │ 3. Bearer token；逻辑 origin 仍为 https://id.re8ch.com
   │    实际连接使用 STALWART_CONNECT_URL
   ▼
stalwart.mail-system.svc.cluster.local:8080
   │
   ├── JMAP Mail / Calendar / Contacts / FileNode
   └── DAV file collections
```

tenant-mcp 保存的只是连接元数据。PKCE verifier、access token 和 refresh token
进入 `stalwart-credentials` Credential Broker，MCP 参数、响应、日志和 Git 均不
返回凭据。绑定键同时包含 Re8ch tenant 和用户 subject；管理员组织身份不能借此
访问应用组织成员的邮箱。

运行配置：

| 环境变量 | 当前含义 |
| --- | --- |
| `COLLABORATION_ENABLED=true` | 发布协作连接和数据工具 |
| `COLLABORATION_AUTH_MODE=auth-hub-pkce` | 普通流程默认 User Hub PKCE |
| `STALWART_URL=https://id.re8ch.com` | 安全校验、JMAP/DAV 响应和授权计划使用的逻辑公开 origin |
| `STALWART_CONNECT_URL=http://stalwart.mail-system.svc.cluster.local:8080` | 仅用于实际出站连接；代码只接受 HTTP `.svc.cluster.local` origin |
| `STALWART_CLIENT_ID=stalwart-webui` | Auth Hub 与 Stalwart 接受的 audience/client |
| `STALWART_AUTHORIZATION_URL=https://auth.re8ch.com/authorize` | Authorization endpoint |
| `STALWART_TOKEN_URL=https://auth.re8ch.com/token` | code exchange 与 refresh endpoint |
| `STALWART_REDIRECT_URI=https://tools.service.re8ch.com/collaboration/oauth/callback` | 预注册的固定回调 |

逻辑 origin 和连接 origin 被刻意分开。JMAP session 返回的 `apiUrl`、DAV href 和
文件引用仍必须与 `STALWART_URL` 同源；tenant-mcp 只在发出请求时把 scheme/host
改写为受控内部 Service。协议响应不能选择新主机，因此此设计不是任意 SSRF
代理，也没有降低公开 origin 的同源校验。

## 3. User Hub OAuth 合约

### 3.1 Authorization request

tenant-mcp 生成一次性随机 `state` 和 PKCE verifier，只发送 S256 challenge。
固定参数如下：

```text
response_type=code
client_id=stalwart-webui
redirect_uri=https://tools.service.re8ch.com/collaboration/oauth/callback
code_challenge_method=S256
scope=openid profile email offline_access
      urn:ietf:params:oauth:scope:mail
      urn:ietf:params:oauth:scope:calendars
      urn:ietf:params:oauth:scope:contacts
      urn:ietf:params:oauth:scope:files
```

产品侧权限仍分别是 `mail.read`、`calendar.read`、`calendar.respond`、
`contacts.read`、`files.read`。provider scope 可能比产品侧只读权限更宽；
tenant-mcp 继续限制具体 JMAP/DAV 方法，不能把 access token 暴露给 Agent。

### 3.2 Callback 与 token exchange

回调必须同时满足：

1. 当前 Re8ch tenant 和 subject 与启动授权时相同；
2. state 完整匹配并只能消费一次；
3. redirect URI、client ID 和 PKCE verifier 来自 Broker 中的启动记录；
4. token response 为 Bearer、具有正数有效期，并且不是管理 token；
5. 使用 access token 读取 JMAP session 后恰好识别一个 personal account；
6. account ID 在刷新期间不发生变化。

回调失败页面不显示 provider body 或 token。运行日志只记录脱敏后的错误类别。
诊断日志由 service-backend PR #2576 加入，禁止扩展为 JWT、邮箱、授权码或响应
正文日志。

### 3.3 Refresh

授权请求包含 `offline_access`。refresh token 只保存在 Broker，access token 到期后
由 tenant-mcp 在数据调用前刷新，再用新的 token 重新验证 JMAP personal account。
生产验收已特意等待首个 access token 过期；之后再次读取邮箱成功，连接 expiry
向后延长，证明不是只验证了首次 code exchange。

第三方 OAuth 实现至少应保证：

- refresh token 可轮换；tenant-mcp 始终保存最新返回值；
- 未返回新 refresh token 时，旧值仍可继续使用；
- refresh 不改变 subject、email 对应账户或 personal account ID；
- access token 的 issuer、audience、签名、有效期、scope 和 token use 被服务端验证；
- revoke 后原连接不能再读取数据，但不删除 Stalwart 账户或内容。

## 4. Stalwart 原生授权与统一入口的关系

Stalwart 原生 OAuth/设备授权必须保留：

- `authorizationMode: device` 仍是 self-service 的显式兼容模式；
- 原生 `/auth/device`、`/auth/token` 和账户安全功能继续属于 Stalwart；
- User Hub 不能要求平时不启用本地密码的用户重新输入 Stalwart 账户密码；
- Stalwart 本地应用密码只用于明确的 IMAP/SMTP/JMAP 客户端，不是 User Hub
  主登录密码；
- 平台 recovery administrator、CLI token 或管理 token 不得作为用户数据 token。

统一 OAuth 第三方接口应当实现一个 broker/issuer 适配层，而不是删除 Stalwart
原生端点。未来若 Stalwart 能直接配置标准外部 issuer，也仍需保留 device flow
作为兼容入口，并分别验证 audience、scope 和账户映射。

## 5. 本次排障经过与结论

### 5.1 初始现象

User Hub 登录和授权码签发成功，但回调统一显示：Auth Hub 已登录，Stalwart 未接受
account token。最初无法区分 token 校验失败、JMAP endpoint 错误或网络故障。

### 5.2 已排除项

- Re8ch Auth 会话有效，用户为 `harry@re8ch.com`，无需开启本地密码；
- Auth Hub authorization code 的签发、消费和 refresh 路径均返回成功；
- Stalwart OIDC Directory issuer 为 `https://auth.re8ch.com`；
- username claim 使用 `email`，accepted audience 包含 `stalwart-webui`；
- Auth Hub access token 代码已携带 scope claim；
- Stalwart 与 tenant-api 时钟一致；
- JMAP、Calendar、Contacts、FileNode 能力在服务器内部 session 中存在；
- `time.LoadLocation("Asia/Shanghai")` 的失败来自 tenant-mcp 镜像缺少 tzdata，
  已通过嵌入 Go `time/tzdata` 修复，而不是日历协议错误。

### 5.3 真正原因

脱敏日志显示首次错误为 `invalid provider response`。进一步探测发现
`https://id.re8ch.com/jmap/session` 和 `/.well-known/jmap` 从公网均返回纯文本 503，
不是 Stalwart 的 JSON 401/403。集群内访问
`http://stalwart.mail-system.svc.cluster.local:8080/.well-known/jmap` 返回正常 JMAP
session。因此 OAuth token 本身不是主因，故障点是公开 Gateway 到已迁往
`overseas-flk` 的 Stalwart backend 路径。

直接把 `STALWART_URL` 改为内部 HTTP 被安全校验正确拒绝；改成
`https://mail.re8ch.com` 虽能完成 OAuth，但 JMAP session 仍返回
`https://id.re8ch.com`，触发同源校验。这两条都不是正确长期方案。

最终方案是新增独立 `STALWART_CONNECT_URL`：保留 `id.re8ch.com` 为协议和产品
origin，只把 tenant-mcp 的网络连接定向到内部 Service。service-backend PR #2578
实现，镜像 PR #2579 发布；observability-gitops PR #60 固化 Helm values、schema、
locked renders 和实际 Deployment 环境变量。

### 5.4 仍未解决的独立问题

截至 2026-10-31，`id.re8ch.com` 的公开 JMAP 路径仍返回 503。它不再阻塞
Re8ch self-service，但会影响直接访问账户页面、公开 JMAP/DAV 客户端以及需要该
入口的原生 OAuth 流程。应按 Cilium Gateway/跨节点 backend 故障单独修复，不能
把内部连接绕行写成“下线 id.re8ch.com”。恢复后需重新验证：

- `/` 到 `/account` 的跳转；
- `/.well-known/jmap`、`/jmap/session`；
- `/dav/cal/`、`/dav/card/`、`/dav/file/` 的发现和鉴权；
- 原生 device authorization；
- 外部客户端 TLS、SNI 和证书链；
- Gateway 从 R640 到 `overseas-flk` endpoint 的可达性。

## 6. 生产验收结果

验收使用 Re8ch tenant self-service 插件和用户自己的 Stalwart 账户。没有将 Admin
组织身份、管理凭据或本地 kube credential 当作用户授权。

| 能力 | 结果 | 证据摘要 |
| --- | --- | --- |
| User Hub PKCE | 通过 | 浏览器复用现有 User Hub 会话；回调页为 `Authorization complete` |
| 连接状态 | 通过 | `collaboration_connection status` 返回 `Ready` |
| token refresh | 通过 | 首个 access token 过期后再次列邮箱成功，expiry 更新 |
| 邮箱列表 | 通过 | Inbox 等 mailbox 可列；验收时 Inbox 348、未读 153 |
| 邮件搜索 | 通过 | 返回真实邮件摘要、分页状态和下一位置；未把正文写入 Git/日志 |
| 日历列表 | 通过 | 默认日历 `Stalwart Calendar (harry@re8ch.com)` 可见 |
| 日历窗口查询 | 通过 | `Asia/Shanghai` IANA 时区不再报错；空窗口被正确返回为空列表 |
| 通讯录 | 通过 | 默认地址簿和 Trusted Senders 可列；空搜索正确返回空列表 |
| Drive 根发现 | 通过 | 返回当前 principal 自有 `/dav/file/.../` collection |
| Drive 单层列表 | 通过 | 根目录可列；验收时目录为空，未声称完成文件正文读取 |
| RSVP 写入 | 未执行 | 需要受控邀请，避免通知任意真实组织者 |
| revoke 后拒绝 | 本轮未执行 | 保留连接供用户继续使用；已有本地隔离测试，仍应另做生产撤销验收 |
| 跨用户/跨 tenant | 本轮未执行 | 需要第二个真实会话，不得复制 token 模拟 |

通过的当前连接引用为不透明用户绑定；它不是凭据，也不应硬编码到 Git 或产品
配置。应用应通过 `collaboration_connection list/status` 取得当前用户自己的绑定。

## 7. 第三方统一 OAuth 接口实施清单

### 7.1 注册信息

每个第三方客户端至少登记：

- 稳定 client ID 和显示名称；
- 精确 redirect URI 列表，不允许通配符；
- 允许的 grant type：Authorization Code + PKCE，必要时 Device Authorization；
- public/confidential client 类型；浏览器和 Agent public client 不保存 client secret；
- 精确 audience/resource；
- scope 到产品能力的映射；
- token、refresh、撤销和审计策略；
- tenant/organization 可见范围及账户绑定规则。

不要开放匿名动态客户端注册，除非另有受审查的平台注册策略。所谓 self-service
注册是用户为自己的已审核服务建立授权绑定，不是让任意应用获得 issuer 信任。

### 7.2 建议标准端点

第三方接口应发布标准 discovery，并稳定提供：

```text
/.well-known/openid-configuration
/authorize
/token
/jwks.json 或 discovery 声明的 jwks_uri
/revoke
可选：/device_authorization、/introspect、/userinfo
```

实际路径以 discovery 为准，tenant-mcp 不应猜测。issuer 必须与 token `iss`
完全一致；JWKS key rotation 要保留足够重叠时间，不能让仍有效的 access token
因旧 key 立即删除而失效。

### 7.3 Token 最小合约

access token 至少需要可验证的：

- `iss`：精确 issuer；
- `aud`：目标服务，例如 `stalwart-webui`，不得接受任意 audience；
- `sub`：稳定且不可因邮箱重命名而改变的用户标识；
- `email` 及验证状态：仅用于当前 Stalwart directory 的 username 映射；
- `iat`、`nbf`、`exp`；
- token use/type；
- 实际批准的 scope；
- 可选 tenant/organization claim，但它不能替代 Re8ch 当前会话的组织校验。

邮箱不是跨组织所有权证明。长期应优先用稳定 subject 映射 Stalwart principal；
若当前版本只能用 email claim，必须保证 email 已验证、唯一且受账户迁移流程控制。

### 7.4 安全和错误处理

- PKCE 只允许 S256；拒绝 plain；
- state 一次性、短期、绑定发起用户和 tenant；
- authorization code 一次性且短期；
- token endpoint 不接受 redirect URI 漂移；
- access token 不放 URL、MCP 参数、浏览器 local storage 或日志；
- refresh token 只进 Broker，支持轮换和撤销；
- 错误响应使用稳定 error code，正文不回显 token/JWT；
- provider 401/403、5xx、非 JSON 和网络超时要分别归类；
- 外部内容始终标记为用户数据，不作为 Agent 指令执行。

## 8. 接手者推荐顺序

1. 不改现有 tenant-mcp 默认流程，先实现第三方 OAuth discovery、client registration
   和 token claim 合约。
2. 用独立测试 client 验证 code + PKCE、refresh rotation、revoke 和 JWKS rotation。
3. 在 Stalwart 测试账户验证 audience/scope/personal account 映射；禁止用管理 token
   代替用户 token。
4. 恢复 `id.re8ch.com` 公网 Gateway，并分别验收公开 JMAP/DAV 与原生 device flow。
5. 给 tenant-mcp 增加 provider 配置时，保持逻辑 origin 与 connect origin 分离；
   外部 provider 不得使用内部 HTTP connect override。
6. 使用当前 `collaboration_connection` plan/start/status/revoke 状态机接入，不新建
   第二套 token 数据库或 MCP 注册服务。
7. 完成受控 RSVP、文件正文、撤销后拒绝、同 tenant 不同用户和跨 tenant 的剩余
   生产验收，再扩大写权限。

## 9. 代码、发布和审计索引

- service-backend PR #2574：Go tzdata，修复 IANA 日历时区。
- service-backend PR #2576：OAuth 回调的脱敏 provider 错误日志。
- service-backend PR #2578：逻辑 origin 与内部 connect origin 分离。
- service-backend PR #2579：发布并锁定 tenant-mcp 镜像
  `sha256:96870f7e2687e69128643b6a376ec138e7e5fec4df5878cf012cbdb2ec2f37fa`。
- observability-gitops PR #60：生产 Helm 配置、schema、locked render 和镜像证据。
- tenant-mcp release run：`re8ch/service-backend` Actions run `41157833160`。
- 诊断日志读取审计：`op_9f86e55f0b7c4feabf21fa14bd447aad`、
  `op_ec434efc36754afc87e9438718a1d43b`。
- 最终运行 Deployment 更新审计：`op_82b03906dfa84892a7499eb14ddc74c0`。

这些引用证明当时的实现和发布，不代替当前运行状态。重新交付时仍需同时验证
GitHub checks、不可变镜像 digest、Deployment Ready、MCP `tools/list` 和真实
`tools/call`。

## 10. 相关文档

- `platform/stalwart/SELF-SERVICE.md`：产品与权限边界。
- `platform/stalwart/SELF-SERVICE-ACCEPTANCE.md`：可重复的真实验收步骤。
- `platform/stalwart/WEBMAIL-SESSION.md`：Webmail SSO、应用密码和 session secret。
- `platform/stalwart/bot-access-contract.yaml`：公开 JMAP/DAV 端点约定。
- `platform/stalwart/BOOTSTRAP.md`：平台管理与 bootstrap 边界。

