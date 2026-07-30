# Video Infra: 机房模拟语音接入 POC

本目录记录 Mac mini、MikroTik、restricted-node-6dc3f140f9、天翼 A8-C 八口语音网关和
Grandstream GXW4108 之间的网络与语音接入实验。目录名沿用项目约定
`video-infra`，当前内容聚焦 PSTN/IMS/SIP 音频链路。

## 当前结论

- 电信 IMS 凭据由 A8-C 管理，用户侧拿不到，不能把 restricted-node-6dc3f140f9 直接注册到电信 IMS。
- A8-C 的 8 个模拟口是 FXS；GXW4108 的 8 个模拟口是 FXO，两者用短 RJ11
  直通电话线连接。
- 实体话机接 A8-C 已验证可呼入、呼出，所以 IMS 注册和 A8-C FXS 基本正常。
- restricted-node-6dc3f140f9 能向 GXW4108 建立 SIP 会话，GXW4108 也会返回 RTP；但 GXW4108
  的一阶段模拟拨号未被 A8-C/电信侧成功接受。
- GXW4108 返回 `200 OK` 只表示 FXO/SIP 桥接会话建立，不表示被叫已振铃。
  A8-C 的 POTS 统计记录了实际呼出失败。

## 阅读顺序

1. [HANDOFF.md](HANDOFF.md) — 给接手同事的诊断手册。
2. [inventory/network-topology.yaml](inventory/network-topology.yaml) — 地址、接口和物理链路。
3. [inventory/hardware.md](inventory/hardware.md) — 型号、版本与能力边界。
4. [configs/](configs/) — 各设备当前配置基线和禁止触碰项。
5. [evidence/experiment-log.md](evidence/experiment-log.md) — 已做实验及其证据。
6. [scripts/gxw_sip_call_test.py](scripts/gxw_sip_call_test.py) — 需显式确认才会发起呼叫的测试脚本。

## 安全边界

- 仓库不保存 Web、SSH、IMS/SIP 密码。
- 未经现场负责人明确确认，不发起真实外呼。
- 不再修改 A8-C 的 IMS 服务器、Voice VLAN、注册账号或策略路由。
- 测试优先只动 GXW4108 的 1 号 FXO，其他 7 路保持业务基线。

