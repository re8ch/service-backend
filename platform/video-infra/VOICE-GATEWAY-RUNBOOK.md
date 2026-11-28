# R640 Voice Gateway 部署与回滚

## 现役八路资产基线

2026-11-14 起，运营商侧现役设备为 `MSG2600E-XES-8E8V-WS3`。GXW4108
必须保持八个独立调度组，R640 gateway 必须保持如下固定对应：

| line | GXW SIP/UDP | 天翼号码 |
|---|---:|---|
| line01 | 5060 | 0738-4643457 |
| line02 | 5062 | 0738-4613840 |
| line03 | 5064 | 0738-4425237 |
| line04 | 5066 | 0738-4496412 |
| line05 | 5068 | 0738-4613432 |
| line06 | 5070 | 0738-4452535 |
| line07 | 5072 | 0738-4213504 |
| line08 | 5074 | 0738-4211561 |

权威现场记录见
[evidence/2026-11-14-eight-line-acceptance.md](evidence/2026-11-14-eight-line-acceptance.md)。
不得把 2–8 口重新合并为 `rr:2-8`；这会破坏固定主叫号码映射。

## 部署前条件

1. 备份 GXW 配置并记录八路当前 SIP 目标端口；不要修改 A8-C。
2. 取得业务 HMAC、Agent Digest 账户以及百炼 ASR/TTS API key/配额。
3. MikroTik 导出脱敏配置，确认禁用 SIP helper 不影响其他 SIP 设备。
4. 在 R640 暂存本目录为 `/opt/video-infra-src`。

## 安装和激活

```bash
sudo REPO_ROOT=/opt/video-infra-src /opt/video-infra-src/scripts/install-r640-freeswitch.sh
sudo vi /etc/re8ch-voice/voice-gateway.env
sudo /opt/video-infra-src/scripts/deploy-r640-voice.sh
sudo /opt/video-infra-src/scripts/verify-r640-voice.sh
```

安装脚本固定 FreeSWITCH `v1.11.1` 及其 Sofia-SIP、SpanDSP 源码提交。激活脚本
先建立 `/var/backups/re8ch-voice/<UTC>` 备份；如 FreeSWITCH 启动失败，会自动
恢复临时自动应答服务。激活成功后，才能把 GXW 目标逐路改为 5160。

## 分阶段验收

- Ch1：20 次入呼、20 次授权外呼、4 Agent、共享 ASR、DTMF、双方挂机。
- Ch2–4：逐口确认 `lineNN` 和固定 FXO，不允许串线。
- Ch5–8：完成 8 通、48 成员、2 小时容量与故障测试。

未取得真实测试号码、业务授权或百炼密钥时，不执行真实外呼，也不得声称
ASR/TTS 已验收。

## 例行复验与证据保留

- 每月逐路做一次无号码摘机/拨号音检查；真实外呼按业务授权单执行。
- 每季度逐路完成一通入呼和一通外呼，核对主叫号码、双向音频和双方挂机。
- GXW、天翼网关、MikroTik、R640 任一设备更换、升级或重新接线后，执行
  完整八路映射验收，不能只做 SIP `200 OK` 检查。
- 记录时间、line、SIP 端口、选择码、响应时序、来显、音频、挂机结果和
  操作人；测试手机号码只进入受控工单，不进入 Git。
- PCAP/WAV 若需长期留存，放入批准的受控证据库并记录 SHA-256；Git 只保留
  路径、摘要、结论和保留策略。

## 断电恢复演练

GXW 配置已验证“保存 → 暖重启 → 重新登录回读”持久化，但现有证据不能证明
整套链路断电后自动恢复。正式断电演练必须安排维护窗口并逐项记录：

1. 断电前导出脱敏配置，确认八路空闲并记录 IMS 注册状态。
2. 依现场供电拓扑断电；不得在有活动呼叫时强制断电。
3. 恢复供电后等待天翼 IMS 八路全部注册成功，再检查 GXW 网络和八个 `rr` 组。
4. 在 R640 确认 FreeSWITCH 服务、5160 监听和八个 gateway。
5. 逐路执行入呼、外呼、来显、双向音频及挂机释放。
6. 只有八路全部通过后，才可以记录“冷启动自动恢复已验收”。

设备 Web/SSH 密码、IMS 密码、HMAC 和 API key 不得写入本手册、命令行日志
或 Git；只记录凭据名称、保管位置和轮换责任。

## 回滚

1. 将已切换 GXW 线路的目标端口恢复为备份值。
2. `sudo systemctl disable --now re8ch-voice-gateway freeswitch`
3. `sudo systemctl enable --now gxw-sip-autoanswer`（或按原记录恢复 Kamailio）。
4. 检查 5160/40002 监听和原线路入呼；保留失败日志与 UTC 备份，不改 A8-C。
## HOMER public observability entrypoint

HOMER is the SIP signalling evidence layer for the voice gateway. Its public
operator entrypoint is:

```text
https://phonycenter.service.re8ch.com:8443/
```

The explicit port is required because the upstream R640 edge currently carries
public HTTPS to Traefik on TCP 8443. Cloudflare DNS is controller-managed and
resolves the host to the MikroTik public address. MikroTik forwards only TCP
8443 to R640 TCP 443; Traefik then proxies the `phonycenter.re8ch.com` host to
the HOMER UI at `192.168.88.251:19080`.

Only HOMER HTTP UI/API traffic is published. Do not publish HEP `19060`, SIP
`5060/5080/5160`, ESL `8021`, RTP, DuckDB, Jitsi, or LiveKit ports through this
entrypoint. HOMER internal authentication remains mandatory.

HOMER provides SIP call identity, endpoints, responses and call-flow evidence.
ASR/TTS content, recordings, Agent authorization and join/leave control belong
to the Voice Operation Console and must be correlated back to HOMER using
`call_id`/SIP `Call-ID`.
