# restricted-node-6dc3f140f9 语音 POC 基线

- 主机局域网地址：`192.0.2.169/24`
- 主机接口：`eno2np1`
- Kamailio：`192.0.2.169:5060/udp`
- GXW 直连临时脚本：`192.0.2.169:5160/udp`
- GXW SIP：`192.0.2.4:5060/udp`
- 测试 RTP 接收端口：脚本 SDP 目前宣告 `40000/udp`

2026-07-30 现场只读检查时，5060/UDP 正在监听，5160/UDP 只有测试脚本运行时
临时绑定。

## 目标职责

restricted-node-6dc3f140f9 最终承载 Asterisk 或 FreeSWITCH，并向上提供稳定的内部 SIP API：

- 每路 FXO 映射成明确的 trunk/channel；
- 呼入路由、呼出路由和并发控制；
- RTP 录音、ASR/TTS、AI Agent；
- CDR、失败原因、线路健康；
- 对 GXW4108 隐藏模拟线路和运营商 IMS 细节。

MikroTik 只负责网络，不承载 PBX 或语音存储。

