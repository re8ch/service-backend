# GXW4108 配置基线与下一组实验参数

## 当前已保存配置

- IP：`192.0.2.4`
- Account 1：`restricted-node-6dc3f140f9 SIP POC`
- SIP Server：`192.0.2.169:5160`
- SIP Registration：No
- Stage Method：`ch1:1;ch2-8:2;`
- Outgoing Call Dial Plan：`{[x#]+}`
- Wait for Dial Tone：`ch1-8:N;`
- Minimum Delay Before Dial PSTN Number：`ch1-8:500;`
- Digit Length：`ch1-8:10;`（100 ms）
- Digit Volume：`-11 dB`
- Round-robin：设备实际保持 `rr:1-8;`
- Dial pause：设备重启后显示 `ch1-8;`

## 端口选择

测试 1 号 FXO 时，SIP Request-URI 的 user 使用：

```text
99 + 1 + 被叫号码
```

例如占位号码 `13800138000` 对应 `99113800138000`。`99` 是 GXW 端口选择
前缀，`1` 是第一口；不要把真实客户号码提交到仓库。

## 建议的下一组单变量实验

只改 1 号口，其他口保持原值：

```text
Minimum Delay Before Dial PSTN Number:
ch1:1500;ch2-8:500;

Digit Length:
ch1:15;ch2-8:10;
```

并把 `Use DTMF via RFC2833/SIP Info` 设为 `No`，确保一阶段拨号时由 GXW
本地在模拟线上产生 DTMF，而不是等待 SIP 对端发送按键事件。保存、重启并
重新打开页面确认字段没有被老固件回滚。

如仍失败，停止调一阶段拨号，转做二阶段实验：先让 GXW 摘机并把 A8-C
拨号音作为 RTP 返回 restricted-node-6dc3f140f9，再由 restricted-node-6dc3f140f9 逐位发送 RFC2833 DTMF。二阶段能把
“摘机失败、无拨号音、按键未生成、按键节奏错误、运营商拒绝”拆开观察。

