# Video Infra: 机房模拟语音接入 POC

本目录记录 Mac mini、MikroTik、restricted-node-6dc3f140f9、天翼 A8-C 八口语音网关和
Grandstream GXW4108 之间的网络与语音接入实验。目录名沿用项目约定
`video-infra`，当前内容聚焦 PSTN/IMS/SIP 音频链路。

## 当前结论

- 电信 IMS 凭据由 A8-C 管理，用户侧拿不到，不能把 restricted-node-6dc3f140f9 直接注册到电信 IMS。
- A8-C 的 8 个模拟口是 FXS；GXW4108 的 8 个模拟口是 FXO，两者用短 RJ11
  直通电话线连接。
- 实体话机接 A8-C 已验证可呼入、呼出，所以 IMS 注册和 A8-C FXS 基本正常。
- restricted-node-6dc3f140f9 已通过 GXW4108 固定 FXO 1 完成 Stage-2 呼出、手机振铃、接听音频
  和挂机释放验证。
- 本地区手机使用 11 位直拨；外地手机按当前运营商提示使用
  `0 + 11 位手机号`。
- 固号入呼已通过 GXW Ch1 转发至 restricted-node-6dc3f140f9 UDP/5160，自动应答提示音和主动
  挂断验证成功。
- restricted-node-6dc3f140f9 已安装并启用宿主机 FreeSWITCH 1.11.1：GXW profile 为 5160、Agent
  profile 为 5080、ESL 仅监听回环地址；临时 Python 自动应答器已停用，
  Kamailio 暂保留在 5060，等待八路真实话路验收后再决定归档。
- GXW4108 返回 `200 OK` 只表示 FXO/SIP 桥接会话建立，不表示被叫已振铃。
  业务成功仍应结合手机现场结果、RTP、GXW Syslog 和 A8-C 只读统计判断。

## 阅读顺序

1. [HANDOFF.md](HANDOFF.md) — 给接手同事的诊断手册。
2. [inventory/network-topology.yaml](inventory/network-topology.yaml) — 地址、接口和物理链路。
3. [inventory/hardware.md](inventory/hardware.md) — 型号、版本与能力边界。
4. [configs/](configs/) — 各设备当前配置基线和禁止触碰项。
5. [evidence/experiment-log.md](evidence/experiment-log.md) — 已做实验及其证据。
6. [FREESWITCH-MULTI-AGENT-ARCHITECTURE.md](FREESWITCH-MULTI-AGENT-ARCHITECTURE.md)
   — 每通最多 4 个真实 SIP Agent、共享 ASR、48 会议成员的长期架构。
7. [VOICE-GATEWAY-RUNBOOK.md](VOICE-GATEWAY-RUNBOOK.md) — restricted-node-6dc3f140f9 安装、分阶段
   验收和回滚流程。
8. [scripts/gxw_stage2_call_test.py](scripts/gxw_stage2_call_test.py) — 固定端口
   Stage-2 DTMF 和 RTP 诊断脚本。
9. [scripts/gxw_sip_autoanswer.py](scripts/gxw_sip_autoanswer.py) — 入呼自动应答
   POC；生产环境应由 FreeSWITCH 替换。
10. [scripts/gxw_sip_call_test.py](scripts/gxw_sip_call_test.py) — 需显式确认才会
   发起呼叫的一阶段测试脚本。

## 安全边界

- 仓库不保存 Web、SSH、IMS/SIP 密码。
- 未经现场负责人明确确认，不发起真实外呼。
- 不再修改 A8-C 的 IMS 服务器、Voice VLAN、注册账号或策略路由。
- 测试优先只动 GXW4108 的 1 号 FXO，其他 7 路保持业务基线。
