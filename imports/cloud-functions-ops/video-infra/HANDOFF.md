# A8-C + GXW4108 拨号诊断交接手册

## 目标

把中国电信托管在 A8-C 内的 8 路 IMS 号码，通过 A8-C FXS → GXW4108 FXO
转成 restricted-node-6dc3f140f9 可消费的 SIP。restricted-node-6dc3f140f9 上的 PBX/AI Agent 不需要理解电信 IMS
注册细节。

```text
电信 IMS
  ↕ A8-C Voice_R_43（运营商管理）
A8-C 8×FXS
  ↕ 8 条 RJ11 模拟线
GXW4108 8×FXO
  ↕ SIP/RTP，192.0.2.16/24
restricted-node-6dc3f140f9 PBX / AI Agent
```

## 当前阻碍

SIP 侧已经能走到 GXW4108，模拟侧却没有完成真实外呼：

1. restricted-node-6dc3f140f9 INVITE 到 GXW 成功。
2. GXW 正确占用指定 FXO，并返回 180/200 和 RTP。
3. A8-C 对应 POTS 口失败计数增加，被叫不振铃。
4. 实体话机接同一 A8-C 口可以拨通。

最可能的故障域按优先级排列：

1. GXW 一阶段拨号没有在模拟线上本地生成 DTMF；
2. 摘机后等待时间太短，A8-C 拨号音尚未稳定；
3. DTMF 位长/位间隔不适合 A8-C；
4. GXW 误判回铃/接通信号，过早向 SIP 返回 200；
5. 中国制式的阻抗、音调检测或断线监督配置不匹配。

## 第一阶段：无外呼的准备

1. 现场确认电话线：A8-C 第 1 口接 GXW FXO0/通道 1。
2. 普通电话机再次接 A8-C 第 1 口，记录：
   - 摘机到拨号音出现的时间；
   - 拨号是否需要 `#` 结束；
   - 拨号后听到的回铃音或失败提示。
3. 在 GXW Status 页面确认 8 个 FXO 均为 Connected/Idle。
4. 在 MikroTik 检查 ether10 链路、DHCP lease 和 SIP helper。
5. 在 restricted-node-6dc3f140f9 开抓包，但此时不发 INVITE：

```bash
sudo tcpdump -ni eno2np1 \
  'host 192.0.2.208 and (udp port 5060 or udp portrange 5000-5200)' \
  -w /tmp/gxw-line1-$(date +%Y%m%d-%H%M%S).pcap
```

## 第二阶段：一阶段拨号单变量测试

只改 GXW 第 1 口：

1. `Stage Method = ch1:1;ch2-8:2;`
2. `Wait for Dial Tone = ch1-8:N;`
3. `Minimum Delay = ch1:1500;ch2-8:500;`
4. `Use DTMF via RFC2833/SIP Info = No`
5. `Digit Length = ch1:15;ch2-8:10;`
6. 保存并重启。
7. 重开页面，逐项截图，确认固件没有静默回滚字段。

获得现场负责人明确同意后，才运行一次测试：

```bash
python3 video-infra/scripts/gxw_sip_call_test.py \
  "991<授权测试号码>" --confirm-call
```

同时观察：

- 被叫是否振铃；
- A8-C 第 1 口成功/失败计数；
- GXW FXO 状态；
- 抓包中 100/180/200/BYE 的时序；
- RTP 里实际听到的是拨号音、忙音、回铃音还是语音提示。

每次只改一个参数，测试间隔至少 30 秒，避免把线路状态残留误判为新结果。

## 第三阶段：二阶段拨号拆解故障

若一阶段仍失败：

1. GXW 第 1 口改为 stage 2。
2. restricted-node-6dc3f140f9 只发起到端口 1 的 INVITE，不把被叫号放进一阶段拨号串。
3. 接收并保存 GXW 返回的 G.711 音频。
4. 音频确认听到 A8-C 拨号音后，restricted-node-6dc3f140f9 逐位发送 RFC2833 telephone-event。
5. DTMF 初值用 150 ms tone + 150 ms gap，最后一位后等待。
6. 对返回音频做频谱/人工听音，记录失败音调。

若二阶段可拨通，说明硬件链路无问题，后续让 Asterisk/FreeSWITCH 负责
stage-2 DTMF；无需继续逆向 GXW 固件。

## 可观测性

### GXW

- 开启 Syslog，目标设为 restricted-node-6dc3f140f9，级别 DEBUG。
- 使用官方 Grandstream Syslog Utility 或 restricted-node-6dc3f140f9 上的 rsyslog 接收。
- 如页面支持，运行 PSTN Detection、Line Analysis、Audio Capture。
- 保存配置导出文件，但密码字段必须脱敏后才能提交。

### A8-C

只读查看“语音配置 → 统计监控 → POTS 呼叫统计”，按测试前后差值判断。
不要把 GXW 的 200 OK 当成呼叫成功。

### restricted-node-6dc3f140f9

建议保留：

- SIP pcap；
- 双向 RTP pcap；
- 解码后的 WAV；
- GXW syslog；
- A8-C 测试前后统计截图；
- 每次实验的参数和准确时间。

## 成功标准

单路 POC 必须同时满足：

1. 指定 A8-C/GXW 端口，不串线；
2. 授权测试手机实际振铃并接通；
3. 双向语音连续 60 秒；
4. 主叫、被叫任一方挂断后 3 秒内释放；
5. DTMF 双向可识别；
6. 连续 20 次呼出成功率至少 95%；
7. 来电号码和线路号能映射到 restricted-node-6dc3f140f9 的固定 trunk。

## 回滚

若 GXW 参数调整导致异常：

1. 仅恢复 [configs/gxw4108-baseline.md](configs/gxw4108-baseline.md) 中的基线。
2. 不重置 A8-C，不触发 IMS 批量注销。
3. 不恢复“改 A8 Outbound 到局域网”的实验。
4. 物理话机直连 A8-C 做业务恢复验证。

## 后续产品化

POC 成功后在 restricted-node-6dc3f140f9 部署 Asterisk 或 FreeSWITCH。GXW4108 只做模拟/SIP
转换；MikroTik 只做网络。若 GXW4108 的老固件仍有摘挂机或音调误判，
替换为国内可采购、明确标注 8×FXO 的 Dinstar DAG 系列或 Grandstream
HT881，采购前让卖家提供准确后缀、端口照片和 FXO 规格页，避免误买
8×FXS 版本。
