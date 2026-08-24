# GXW、FreeSWITCH 与多媒体 Agent 语音平台架构

## 生产基线

R640 是唯一语音计算节点。FreeSWITCH 以宿主机 systemd 服务运行并直接绑定
`192.168.88.251`，不经过 K3s/Cilium。MikroTik 仅负责交换、防火墙、SIP helper
管理和 VPN/远程访问，不承载媒体、识别、合成或会话状态。

每通电话允许最多 4 个真实 SIP/RTP Agent 自由混音。Agent 是会议媒体成员，
可独立发声；平台共享一路会议混音给 ASR/观测服务，但不执行唯一发言者仲裁，
也不保证只有一路 TTS。8 路满载容量为：

```text
8 calls × (1 external leg + 4 Agent legs + 1 ASR/observer leg)
        = 48 conference members
```

本基线取代此前“Agent 仅消费文本、协调器唯一 TTS”的方案。

## 数据流与边界

```text
A8-C FXS ↔ GXW4108 FXO ↔ SIP/PCMA ↔ FreeSWITCH conference
                                             ├─ external line
                                             ├─ SIP Agent 1..4
                                             └─ shared 8 kHz mix → Bailian ASR

Agent TTS → Agent 自己的 SIP leg → conference（可与其他 Agent 自由混音）
```

- FreeSWITCH 负责 SIP、RTP、会议、DTMF、早期媒体、双向挂机、超时和 CDR。
- Voice API 负责业务签名、租户/线路授权、幂等、线路锁、加退会和事件序列。
- Media service 只抽取一份会议混音送阿里云百炼流式 ASR，并把 partial/final
  转写广播给已授权 Agent。
- TTS 按 Agent 鉴权；barge-in 只中止对应 Agent 的播放，不静音其他成员。
- 默认不保存原始音频；仅保存同意状态、转写时间边界、CDR 和质量指标。

## 网络与媒体约束

| 用途 | 地址/端口 | 约束 |
| --- | --- | --- |
| GXW trunk | `192.168.88.251:5160/udp` | 仅允许 `192.168.88.249` |
| Agent SIP | `192.168.88.251:5080/udp` | 仅内网/VPN、Digest、禁止匿名 |
| ESL | `127.0.0.1:8021/tcp` | 随机强密码、不得远程暴露 |
| RTP | `192.168.88.251:20000-20255/udp` | GXW 与授权 Agent 网段 |
| Voice API | `192.168.88.251:18092/tcp` | 业务系统签名访问 |

所有媒体强制 `PCMA/8000/1`、20 ms 分包；不自动接受 PCMU、Opus 或视频。
单向 PCMA 含 IP/UDP/RTP 开销约 80–100 kbit/s，R640 的主要容量压力来自
并发 ASR、LLM 和最坏每通 4 路 TTS，而非网络带宽。

GXW 入呼 User ID 固定为 `line01`–`line08`，统一投递 5160。八个出呼 gateway
分别命中 GXW 5060–5067，并保留已验证的 Stage-2 摘机、拨号音检测和 RFC2833
DTMF。A8-C IMS、Voice VLAN 和运营商参数保持只读。

## 会话和事件契约

每个 `call_id` 对应一个会议，硬限制为 1 个外线、4 个 Agent 和 1 个共享
ASR/观测 leg；第五个 Agent 或溢出成员返回 `486 Busy Here`。事件至少包括
呼叫状态、线路、Agent 加退会、`asr.partial/final`、DTMF、RTP 质量、TTS 状态
和挂机原因。每条事件必须携带 `call_id`、单调递增序号、UTC 时间、来源和
幂等键。

业务外呼只接受带 HMAC 签名的 `POST /v1/voice/calls`，并校验 `request_id`、
租户、授权过期时间、线路范围、并发和频率。相同 `request_id` 返回原结果；
平台不能接受未经现有业务系统授权的真实外呼。

## 可靠性、安全和容量门槛

- 禁用 MikroTik SIP helper 前先导出脱敏配置并确认无其他 SIP 依赖。
- 不向公网映射 5060、5080、ESL 或 RTP；远程 Agent 沿用 VPN/ZeroTier。
- 每通设置无 RTP 超时、最大时长和双向释放；按 FXO 建立原子线路占用锁。
- 监控活跃通话/成员、线路占用、SIP 响应、RTP 丢包/抖动、Agent 注册、
  ASR/TTS P50/P95、百炼错误率、拒绝原因和异常挂机。
- 8 通、48 成员持续 2 小时不得出现串线、僵尸会议或端口泄漏；每路至少
  20 次入呼和 20 次外呼，成功率不低于 95%。
- ASR final P95 目标为 700 ms；Agent/TTS 首包按实际多媒体模式分别记录，
  不再用“停顿至唯一回复音频”作为硬指标。

## 分阶段切换与回滚

按 Ch1、Ch2–4、Ch5–8 分阶段切换，每阶段达到稳定性门槛后再扩容。切换前
备份 Kamailio、GXW 和现有服务状态；Kamailio 在八路验收完成前保留。回滚时
恢复 GXW 原目标端口并重新启用 `gxw-sip-autoanswer` 或 Kamailio，不修改 A8-C。

百炼生产 API key、流式 ASR/TTS 权限与配额必须在真实媒体验收前由运维提供；
密钥只进入 root-only 环境文件或 Secret，仓库仅保存模板。
