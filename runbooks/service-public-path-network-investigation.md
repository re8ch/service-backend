# RE8CH service 公网链路故障研究 Runbook

状态：2026-11-24 现场证据汇总，供网络、DNS、代理、Kubernetes 和 MCP 专家联合复现。
本 Runbook 默认只读；任何 DNS、EdgeOne、RouterOS、Gateway、HAProxy、NetworkPolicy
或集群写入必须另走审核与回滚流程。

## 目标与判定标准

目标不是让某一次 `curl` 成功，而是证明 `*.service.re8ch.com` 在中国大陆、国际网络、
本机代理和 MCP connector 四个入口使用可解释、最短且可恢复的公网路径。

完成标准：

1. 权威 DNS、递归 DNS、客户端实际解析结果一致，国内/国际分流有明确策略及证据。
2. 本机直连和代理请求均能在 10 秒内完成 DNS、TCP、TLS 和首字节阶段。
3. MCP connector 与普通 HTTPS 使用同一公开 origin，不依赖 ZeroTier、AmongCluster、
   内部 Service DNS 或跨集群反向隧道。
4. 每个公网 hostname 只有一个明确的边缘 authority；跨集群访问在应用层使用公网服务。
5. 节点或路由器重启后可自动恢复，不需要删除 Pod、重启 agent 或人工刷新 HAProxy 状态。

## 不可违反的网络权威

R640 公网权威仅来自
`operations/network/manifests/network/public-mesh/inventory.json`：

`223.159.210.222 -> MikroTik RouterOS DNAT/eBGP -> 10.251.0.4/Cilium Gateway`

`192.168.88.251` 是 fallback backend。`10.181.22.128` 是 ZeroTier/dummy 服务身份，
不是公网 endpoint。不得用临时 host route、dummy 地址或 R640 fallback 替代权威路径。

Atlantic 是独立 K3s 集群：控制面 `2.29.219.31`，FLK 公网边缘 `2.28.106.230`。
Stalwart 留在 Atlantic；其公网入口不得依赖主集群 AmongCluster 转发。

## 已观察到的故障族

| 故障 | 外部表现 | 已确认原因或边界 |
| --- | --- | --- |
| EdgeOne 欠费/重置后 auth 不可用 | 登录页或回调失败 | CDN/边缘状态与源站健康必须分开验证 |
| R640、RouterOS 断电重启 | Gateway/后端恢复不一致 | 必须检查 DNAT、eBGP、Cilium Gateway 与后端 readiness |
| GitHub-hosted Cloud SSA | Kubernetes API TLS handshake timeout | 本地公网 API 可达不代表 GitHub runner 路径可达 |
| AmongCluster broker | 502、timeout，ImportedService 仍显示 Ready | 控制面 Ready 不是实际数据面传输证据 |
| Atlantic edge 到 Stalwart | TCP 可建立，TLS reset/EOF | K3s NetworkPolicy、旧 HAProxy backend health 和 Pod 重建共同影响 |
| `id.re8ch.com` | ClientHello 后 EOF | FLK edge Pod 的 HAProxy 后端状态未恢复；重建正确节点 Pod 后恢复 |
| `mail.re8ch.com` | TLS EOF/timeout | SNI 曾错误落入 `main_public_edge`；后来又因 DNAT 后 8443 未获 egress 放行而拒绝 |
| Re8ch MCP connector | endpoint 普通 HTTPS 返回 401，但插件报 `Connection failed` | 证明公网 origin 存活；故障位于 connector 出口、代理或其上游连接阶段 |
| Org context 切换 | 切换成功后查询与恢复请求失败 | `tools.service.re8ch.com/tenant-native/mcp` connector 网络错误，不是组织授权结论 |

## 当前已验证状态

- `id.re8ch.com` 与 `mail.re8ch.com` 解析到 `2.28.106.230`。
- 两者使用同一张包含 `id.re8ch.com`、`mail.re8ch.com` 的证书。
- `id.re8ch.com/.well-known/openid-configuration` 为 200。
- `id.re8ch.com/.well-known/jmap` 跟随重定向后为 200。
- `mail.re8ch.com/api/health` 连续六次为 200；WebMail 首页为 200。
- `tools.service.re8ch.com/tenant-native/mcp` 从本机普通 HTTPS 可达并返回 401，TLS 校验成功；
  同时 MCP connector 曾返回 `McpServerError: Connection failed`。
- Atlantic edge 的 `id` SNI 指向 Stalwart 443，`mail` SNI 指向 WebMail TLS proxy 443。
- K3s NetworkPolicy 必须同时允许 Service 443 和 DNAT 后的 WebMail targetPort 8443。

## 2026-11-24 地域与代理实测

结论是“地域策略已经声明，但尚未完成安全验收”，不能称为已完成分流。

- `platform/public-edge-manager/release.yaml` 当前策略为：CN 首选 `223.159.210.222`，
  US 首选 `179.255.126.104`，EU 首选 `2.28.106.230`。
- 当前 `globalpingQualityMode` 是 `Shadow`，质量结果不会阻止错误候选进入 DNS 回答。

| Region | 首选 | 次选 |
| --- | --- | --- |
| CN | `223.159.210.222` | `2.28.106.230` |
| US | `179.255.126.104` | `2.28.106.230` |
| EU | `2.28.106.230` | `179.255.126.104` |
- Atlantic/海外节点固定访问 `179.255.126.104:443` 均 TCP connect timeout；固定访问
  `223.159.210.222` 可正常取得未认证 MCP 401。
- Mac/国内通过 1.1.1.1、8.8.8.8、223.5.5.5、119.29.29.29 查询时得到
  `223.159.210.222`。overseas-flk 上 1.1.1.1 曾返回 `223.159.210.222`，而
  8.8.8.8 返回 `179.255.126.104`，TTL 30。
- 不同递归器观察到不同 SOA serial；权威 `ns1.he.net` 与父区实际委派也不一致。父区当前
  指向三台自建 authority，而不是 HE。自建 authority 中至少一台 UDP/53 从 Mac 超时、
  TCP/53 正常，其他节点与地域也出现过单侧超时。
- macOS 系统 HTTP、HTTPS、SOCKS 和 PAC 均关闭；到 `223.159.210.222` 的实际路由经过
  `utun5` (`172.19.0.1`)，证明请求受透明 TUN 接管，而不是普通 proxy env。
- `NO_PROXY` 不包含 `.service.re8ch.com`，且透明 TUN 不受 `NO_PROXY` 控制。历史审计曾记录
  Mac 默认 DNS 返回 `198.18.0.0/15` fake-IP；当前采样没有复现 fake-IP，但 DNS 仍偶发超时。
- Mac 普通 `curl` 五次中一次停在 DNS resolve 并于约三秒超时；使用
  `--resolve tools.service.re8ch.com:443:223.159.210.222` 为 5/5 成功，TLS 约 9–12 ms；
  绑定 `en0` 并固定地址同样 5/5 成功。
- Mac 固定 `179.255.126.104` 或 `2.28.106.230` 时，默认 `utun5` 路径可返回 401，
  但使用 `--interface en0` 无代理直连时两者都在 TLS 阶段超时；`223.159.210.222`
  经 `en0` 仍约 10 ms 正常。透明代理在这里掩盖了候选 edge 的公网不可达。
- overseas-flk 普通请求 3/3 DNS resolve timeout；固定 `223.159.210.222` 为 3/3 返回
  预期 401。FLK/LA 无代理直连 `179.255.126.104` 和 `2.28.106.230` 均超时；LA/LA2
  也观察到 1.1.1.1 与 8.8.8.8 单侧超时，而固定 `223.159.210.222` 成功。

因此当前 MCP `Connection failed` 是 DNS/连接层统一错误，不是 HTTP 401，也没有证据指向
tenant-mcp 应用处理失败。

还必须把两条客户端路径分开。Re8ch App/MCP connector 的请求很可能由 OpenAI/Codex
云端连接器发出，而不是从 Mac 发出；工具调用元数据曾显示 San Jose。更直接的解释是云端
connector 使用 US 递归/出口，命中 US 首选 `179.255.126.104` 后超时；同一时刻 Mac 经
CN 答案 `223.159.210.222` 得到 401。Mac TUN/DNS 偶发超时是另一项故障，不能假定
`NO_PROXY` 或本机代理设置会影响云端 connector。

后续合成探测必须明确分成：

- A：OpenAI/Codex 云端 connector → US GeoDNS → US 候选 edge。
- B：Mac → `utun5` 透明 TUN → CN DNS → `223.159.210.222`。

## 一次完整采样

在故障发生时记录 UTC 时间、客户端位置、网络运营商、是否启用代理，以及以下结果。
不得记录 OAuth token、Cookie、邮箱正文或 Secret。

```bash
date -u
scutil --proxy
env | rg -i '^(http|https|all|no)_proxy='
dig +short tools.service.re8ch.com A
dig +short tools.service.re8ch.com CNAME
curl -sv --connect-timeout 5 --max-time 15 \
  -o /dev/null https://tools.service.re8ch.com/tenant-native/mcp
curl -sv --noproxy '*' --connect-timeout 5 --max-time 15 \
  -o /dev/null https://tools.service.re8ch.com/tenant-native/mcp
openssl s_client -connect tools.service.re8ch.com:443 \
  -servername tools.service.re8ch.com -verify_return_error </dev/null
```

用 `curl -w` 分解阶段耗时：

```bash
curl -sS -o /dev/null --connect-timeout 5 --max-time 20 \
  -w 'ip=%{remote_ip} dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} code=%{http_code}\n' \
  https://tools.service.re8ch.com/tenant-native/mcp
```

分别在大陆 ISP、国际 VPS、GitHub-hosted runner、Mac 本机直连、本机系统代理和 MCP
connector 环境执行。401 是未携带 OAuth 时的预期应用响应，不能记为网络失败。

## 分层定位

### 1. DNS 与分流

查询权威 NS、国内递归、国际递归及 DoH。记录 TTL、A/AAAA/CNAME、EDNS Client
Subnet 影响和实际命中的边缘 IP。若宣称已完成国内/国际分流，必须给出同一时刻至少
两个地域的权威证据，而不是只读 Git 声明。

检查项：

- 是否由 EdgeOne、Cloudflare、PublicEdge Manager 或权威 DNS 同时写同一记录。
- `service.re8ch.com` 是否有地域策略，还是所有地域都回源 `223.159.210.222`。
- 代理使用本地 DNS 还是远端 DNS；是否出现 DNS 泄漏、污染或错误 IPv6 优先。
- TTL 到期、欠费恢复和记录切换期间是否保留旧 origin。

### 2. 本机代理

对同一 URL 比较：系统代理、显式 `HTTPS_PROXY`、`--noproxy '*'` 和代理远端 DNS。
记录代理软件路由规则命中、出站节点、SNI、连接复用和超时阶段。代理“已开启”不代表
所有进程都使用代理；Codex/MCP connector 可能读取不同的系统代理接口或独立运行环境。

重点验证：

- `NO_PROXY` 是否错误包含 `.re8ch.com`、`.service.re8ch.com` 或过宽 CIDR。
- connector 是否支持 PAC、SOCKS、HTTP CONNECT，以及是否继承桌面 App 环境。
- 代理是否拦截 TLS、丢弃长连接、HTTP/2 或 SSE。
- 代理出口是否回流中国公网 authority，形成国际 → 中国 → 内网 → 跨集群的发夹路径。

### 3. 公网边缘与源站

在边缘记录 SNI、frontend、backend、健康检查结果和终止原因。HAProxy 的 TCP accept
日志只能证明到达 frontend，不能证明后端握手成功。对 Service IP 和 Pod IP 分别做
TCP 与 TLS 测试，识别 Service DNAT 与 NetworkPolicy targetPort 差异。

### 4. Kubernetes 与节点重启恢复

核对节点 Ready、k3s-agent 日志、DaemonSet 所在节点、EndpointSlice、NetworkPolicy、
conntrack 和 HAProxy backend health。删除 Pod 只能作为止血；必须解释为何健康检查未自动
把 backend 从 DOWN 提升为 UP。

### 5. MCP connector

当普通 `curl` 得到 401 而 connector 报网络错误时，收集 connector 的 DNS、代理选择、
remote IP、TLS 错误和请求时间。不要把 401 误判为服务宕机，也不要通过复制 token 到本机
绕过 connector。检查 OAuth session、Org context 和网络错误必须分开。

## 优先假设与破解顺序

1. **GeoDNS 候选健康门失真**：`179.255.126.104` 和 `2.28.106.230` 对
   `tools.service.re8ch.com` 的无代理公网 HTTPS/SNI 不可达，却仍可成为地域答案。
2. **海外递归 DNS/UDP53 路径不稳定**：多个海外节点固定公网 IP 成功、普通解析失败。
3. **Mac 透明 TUN 的 DNS 路径偶发超时**：系统 proxy 开关和 `NO_PROXY` 不能解释该路径。
4. **多层边缘 writer 冲突**：EdgeOne、PublicEdge Manager、external-dns 或静态记录互相覆盖。
5. **跨集群链条仍参与服务请求**：应用没有完全公网化，AmongCluster Ready 假象掩盖失败。
6. **重启后的 stateful dataplane 恢复缺口**：HAProxy health、conntrack、eBGP 或 Gateway 状态陈旧。
7. **TLS/HTTP 协议差异**：connector 使用 HTTP/2、长连接或不同 CA/IPv6 路径。

每次只改变一个变量；先用 DNS/TCP/TLS/HTTP 四层证据证伪，再做配置变更。

### 建议的第一阶段止血

在 GeoDNS 健康门和三台 authority 的 UDP/TCP 可达性修复前，将 `service-pilot` 各地域
临时收敛到已经通过外部 HTTPS 探测的 `223.159.210.222`。随后逐个引入 Atlantic
PublicEdge；任何候选 IP 只有在相应地域的**无代理直连** DNS、TCP、TLS、HTTP/SNI 合成
探测全部通过后才可
进入回答集合。该建议需要单独的变更计划、影响评估和回滚，不由本只读 Runbook 自动执行。

## 目标架构

- 所有跨集群用户态服务发布稳定公网 HTTPS origin。
- Atlantic Stalwart、主集群工具和其他区域服务通过公网鉴权互访。
- AmongCluster 只用于明确需要的控制面元数据或服务发现，不作为公网请求的隐式数据面。
- connector、浏览器、CLI 和集群内部工具使用同一公开 hostname 与 OAuth audience。
- 国内/国际分流在一个 DNS/边缘 authority 中表达，并提供持续合成探测。
- 每个 hostname 记录 `DNS authority -> edge -> origin -> Service -> Pod` 的唯一映射。

## 专家交付物

专家应提交：地域 DNS 矩阵、代理/直连耗时矩阵、connector 与 curl 的路径差异、至少一次
失败抓包或等价连接日志、确认的根因、最小修复、回滚方案，以及公网化后可删除的隧道、
转发器和重复 DNS writer 清单。

