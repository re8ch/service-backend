# 硬件与版本清单

## 天翼 A8-C / MSG2200-T4

- 页面产品名：天翼宽带政企网关
- 页面型号：MSG2200-T4
- 现场标签型号：天翼宽带政企网关 A8-C
- 硬件版本：K
- 软件版本：V0300-0200509-E
- 子版本：3.30.314.20230602
- 能力：IMS-SIP、SIP、H.248；8 路 FXS/POTS；独立 Voice_R_43 和管理接口。
- 管理：LAN1 为 `192.0.2.54/24`，A8-C 为 `192.0.2.12`。
- 已验证：实体话机接入后可呼出、可接听；八个号码已由电信注册。

能力边界：A8-C 是运营商托管的 IMS 客户端和 FXS 网关。其 Web UI 没有提供
把已注册线路反向导出为用户侧 SIP trunk 的功能。把 Outbound Server 改成
restricted-node-6dc3f140f9/MikroTik 会中断运营商注册，不能作为长期方案。

## Grandstream GXW4108

- 型号：GXW4108
- 硬件：2.0 Rev A（SIP User-Agent 报文中显示 `HW 2.0`）
- 固件：192.0.2.205
- 能力：8 路 FXO 到 SIP，支持一阶段/二阶段拨号、G.711、RFC2833。
- 状态：Grandstream 已 EOL；192.0.2.205 是官方最终固件。
- 网络：`192.0.2.4`，MikroTik ether10。
- 物理：FXO0—FXO7 分别接 A8-C 的 8 个 FXS 口。

已知限制：老固件字段解析严格；例如尝试把轮询字段改为 `rr:1;` 后会恢复成
`rr:1-8;`。端口选择测试应使用 GXW 的 `99 + 端口号 + 被叫号码` 前缀，
而不是依赖 round-robin。

## MikroTik RB4011

- 型号：RB4011iGS+
- RouterOS：7.23.2 stable
- 角色：三层管理、局域网交换、到 A8-C 管理面的连通。
- 不承担：Asterisk/FreeSWITCH、录音、ASR 或呼叫业务状态。

RouterOS SIP helper/ALG 会重写 SIP/SDP 地址与端口，容易破坏本地 PBX、
NAT 和 RTP 调试。POC 应确认其状态并优先禁用；禁用前先导出配置，且不能
影响其他现网 SIP。

## Dell PowerEdge restricted-node-6dc3f140f9

- 地址：`192.0.2.169/24`，接口 `eno2np1`
- 角色：未来的 Asterisk/FreeSWITCH、AI Agent、录音、ASR/TTS 和业务逻辑。
- 当前：Kamailio POC 监听 `192.0.2.169:5060/udp`。
- 临时直连测试：Python 脚本使用 `192.0.2.169:5160/udp`。

## 线材

A8-C FXS 与 GXW4108 FXO 使用 RJ11 直通模拟电话线。语音只使用中间一对
线芯；常见 6P2C、6P4C、6P6C 只要中间一对直通都可工作。现有 15 cm
6P6C 短线可以使用，但应逐条用万用表确认不是交叉线。
