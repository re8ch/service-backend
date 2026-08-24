# GXW4108 配置基线与下一组实验参数

## 当前已保存配置

- IP：`192.168.88.249`
- Account 1：`R640 SIP POC`
- SIP Server：`192.168.88.251:5160`
- SIP Registration：No
- Stage Method：`ch1-8:2;`（2026-07-30 二阶段诊断状态）
- Outgoing Call Dial Plan：`{[x#]+}`
- Wait for Dial Tone：`ch1-8:N;`
- Minimum Delay Before Dial PSTN Number：`ch1:1500;ch2-8:500;`
- Use DTMF via RFC2833/SIP Info：`No`
- Digit Length：`ch1:15;ch2-8:10;`（第 1 口 150 ms，其余 100 ms）
- Dial Pause：`ch1:d1p15,d2p15,d3p15,d4p15,d5p15,d6p15,d7p15,d8p15,d9p15,d10p15;ch2-8;`
- Digit Volume：`-11 dB`
- Round-robin：`rr:1;rr:2-8;`（Stage 2 下将 FXO 1 隔离为独立调度组）
- Syslog Server：`192.168.88.251`
- Syslog Level：`DEBUG`（诊断期间临时启用）
- Unconditional Call Forward User ID：`ch1:autoanswer;ch2-8:;`
- Unconditional Call Forward SIP Server：`ch1-8:p1;`
- Unconditional Call Forward Destination Port：`ch1:5160;ch2-8:5060;`

来电转发修改前的回滚值为：

```text
User ID: ch1-8:;
SIP Server: ch1-8:p1;
SIP Destination Port: ch1-8:5060;
```

只为第 1 口启用自动应答 POC；A8-C 配置未改动。

## 端口选择

测试 1 号 FXO 时，SIP Request-URI 的 user 使用：

```text
99 + 1 + 被叫号码
```

例如占位号码 `13800138000` 对应 `99113800138000`。`99` 是 GXW 端口选择
前缀，`1` 是第一口；不要把真实客户号码提交到仓库。

## 已应用的单变量实验参数

2026-07-30 曾只改 1 号口并重启复核，其他口保持原值：

```text
Minimum Delay Before Dial PSTN Number:
ch1:1500;ch2-8:500;

Digit Length:
ch1:15;ch2-8:10;
```

`Use DTMF via RFC2833/SIP Info` 已设为 `No`。第 1 口前 10 个位间隔通过
`d1p15` 至 `d10p15` 设为 150 ms。保存、重启并重新打开页面后，字段未被
老固件回滚。

随后为二阶段诊断把第 1 口切换为 Stage 2；由于 2–8 原本就是 Stage 2，
页面当前显示 `ch1-8:2;`。实测 Stage 2 会忽略仅适用于 Stage 1 的
`99 + 端口号`选择语法，不能用 `991` 固定第 1 口。

2026-07-30 将 round-robin 拆为 `rr:1;rr:2-8;`，保持 Local SIP Listen
Port 为 `ch1-8:5060++;`。重启后字段未回滚；连续三次向 UDP/5060 发起
不带 DTMF 的 Stage-2 摘机验证，Syslog 均为 `FXO: Port: 0`，证明该分组
可以稳定固定物理第 1 口。

如仍失败，停止调一阶段拨号，转做二阶段实验：先让 GXW 摘机并把 A8-C
拨号音作为 RTP 返回 R640，再由 R640 逐位发送 RFC2833 DTMF。二阶段能把
“摘机失败、无拨号音、按键未生成、按键节奏错误、运营商拒绝”拆开观察。
