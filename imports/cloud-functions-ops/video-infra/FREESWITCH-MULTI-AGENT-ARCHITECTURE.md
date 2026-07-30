# GXW、FreeSWITCH 与多 Agent 语音平台架构

## 目标

将 8 路 A8-C/GXW 模拟外线接入 FreeSWITCH，在每通电话中只执行一次流式
ASR、一次发言协调和一次 TTS，同时允许多个文本 Agent 共享同一会话上下文。
媒体面统一采用 G.711 A-law（PCMA，RTP payload type 8）。

## 推荐数据流

```text
A8-C FXS
  ↕ 模拟语音
GXW4108 FXO
  ↕ SIP + PCMA RTP
FreeSWITCH
  ├─ 外线 leg
  ├─ 会议室 / 播放与录音
  └─ 单个 Media Bot leg
       ├─ 单路流式 ASR
       ├─ 共享会话总线
       │    ├─ Agent A：知识检索
       │    ├─ Agent B：决策与业务操作
       │    ├─ Agent C：风险与合规检查
       │    └─ Agent D：补充建议
       ├─ 发言协调器
       └─ 单路流式 TTS
            ↕ PCMA RTP
         FreeSWITCH → 电话
```

Agent 不作为 FreeSWITCH 媒体成员。它们订阅同一份增量转写和结构化会话
事件，输出候选文本、工具结果和风险标记。只有 Media Bot 与 FreeSWITCH
交换音频。这样每通电话通常只有外线 leg 和一个媒体 leg，而不是外线加
5 个 Agent 音频 leg。

## 组件边界

### FreeSWITCH

- 负责 SIP 对话、RTP、会议、录放音、DTMF、早期媒体和挂机释放。
- 为 GXW 建立独立 trunk，并按 FXO 端口映射 DID/线路身份。
- 入呼创建 `call_id` 和会议室；出呼根据区域规则规范化号码。
- 媒体统一协商 `PCMA/8000/1`，避免无意义转码。
- 通过 ESL 或事件 Socket 把呼叫状态发布给会话控制器。

### Media Bot

- 每通电话恰好一个。
- 从 FreeSWITCH 获取单路 8 kHz 音频并送入流式 ASR。
- 将协调器最终文本送入流式 TTS，再把音频写回会议室。
- 实现 barge-in：检测到用户重新说话时停止当前 TTS 播放。
- 保存时间戳、ASR 分段和播放边界，不默认保存原始音频。

### 共享会话总线

建议使用 NATS JetStream；已有 Redis 基础设施时也可先用 Redis Streams。
主题按 `voice.session.<call_id>.*` 隔离，至少包含：

- `call.state`
- `audio.vad`
- `asr.partial`
- `asr.final`
- `agent.request`
- `agent.candidate`
- `agent.tool_result`
- `coordinator.decision`
- `tts.started` / `tts.completed`
- `risk.blocked`

所有事件携带单调递增序号、产生时间、截止时间、来源和幂等键。Agent 只读
取所需上下文，不能直接向电话播放音频。

### 发言协调器

- 在低延迟截止时间内收集候选答案。
- 风险检查拥有阻断权；业务操作必须带幂等键和明确执行结果。
- 合并重复内容，选择一条最终答复交给 TTS。
- 超时采用降级答复，例如先说“正在查询”，避免长时间静音。
- 对需要人工确认的高风险操作转人工或要求电话侧二次确认。

## 号码与端口模型

- 每个 GXW FXO 口对应一个 FreeSWITCH gateway/trunk 身份。
- 入呼 DID 映射由 GXW `Unconditional Call Forward User ID` 明确指定。
- 出呼不依赖 Stage-2 的 `99+端口`；生产环境优先由 FreeSWITCH trunk
  和独立 SIP 监听端口固定 FXO。
- 号码规范化在 FreeSWITCH dialplan 完成：本地区手机使用 11 位，外地
  手机按当前运营商要求增加前缀 `0`。规则应配置化并保留原始拨号串审计。
- A8-C 继续作为只读运营商 IAD，不纳入自动配置管理。

## 容量与网络

单向 PCMA RTP 按 20 ms 分包时，含 IP/UDP/RTP 开销约 80–100 kbit/s。
8 路外线全双工通常低于 2 Mbit/s。4 核 x86、8 GB 内存足以承载 8 路
FreeSWITCH 媒体、会议和事件处理。

容量规划应以并发 ASR、TTS 和模型推理为主，而不是以 Agent 数量乘以媒体
成员数。多个 Agent 共享文本事件后，FreeSWITCH 会议成员规模远低于 48；
即使未来加入监听、录音或人工坐席，也应分别预算媒体 leg。

## 延迟目标

| 环节 | P50 目标 | P95 目标 |
| --- | ---: | ---: |
| GXW 检测振铃至 FreeSWITCH INVITE | 1.0 s | 2.5 s |
| 语音结束至 ASR final | 300 ms | 700 ms |
| Agent 首个可用候选 | 500 ms | 1.5 s |
| 协调与风险检查 | 150 ms | 400 ms |
| TTS 首包 | 250 ms | 700 ms |
| 用户停顿至首个回复音频 | 1.2 s | 3.0 s |

优化顺序应是先测量再调整：Caller ID 等待振铃数、VAD 结束判定、ASR
endpointing、Agent 截止时间、TTS 首包和 FreeSWITCH jitter buffer。不要
通过缩短 GXW 模拟摘机/DTMF 时序来换取几十毫秒，因为这会降低外呼成功率。

## 可靠性与安全

- GXW 仅允许与 restricted-node-6dc3f140f9/FreeSWITCH 的 SIP 和 RTP 地址通信。
- SIP/RTP 使用独立 VLAN 或防火墙白名单；不把 GXW 管理面暴露到公网。
- FreeSWITCH 为每通电话设置最大时长、静音超时和无 RTP 超时。
- 工具型 Agent 使用最小权限、幂等操作和审计日志。
- 记录同意状态；涉及录音和转写时播放告知并遵循适用法律和运营商要求。
- 监控 ASR/TTS 延迟、RTP 丢包/抖动、异常挂机、FXO 占线和号码失败原因。

## 分阶段实施

1. 用 FreeSWITCH 替换临时 Python 自动应答器，完成 Ch1 PCMA 入呼、提示音、
   DTMF 和双向 60 秒通话。
2. 建立 Ch1 出呼 dialplan、区域号码规范化、挂机监督和 20 次稳定性测试。
3. 接入单 Media Bot、VAD、流式 ASR/TTS，先使用单 Agent。
4. 引入会话总线和多 Agent 协调器，Agent 保持纯文本参与。
5. 扩展到 8 路，逐口建立 DID/trunk 映射、容量保护和故障隔离。
6. 达到监控与回滚门槛后，再考虑降低来电振铃等待或 ASR/TTS 延迟参数。
