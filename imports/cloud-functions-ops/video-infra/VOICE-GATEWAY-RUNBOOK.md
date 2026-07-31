# restricted-node-6dc3f140f9 Voice Gateway 部署与回滚

## 部署前条件

1. 备份 GXW 配置并记录八路当前 SIP 目标端口；不要修改 A8-C。
2. 取得业务 HMAC、Agent Digest 账户以及百炼 ASR/TTS API key/配额。
3. MikroTik 导出脱敏配置，确认禁用 SIP helper 不影响其他 SIP 设备。
4. 在 restricted-node-6dc3f140f9 暂存本目录为 `/opt/video-infra-src`。

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

## 回滚

1. 将已切换 GXW 线路的目标端口恢复为备份值。
2. `sudo systemctl disable --now re8ch-voice-gateway freeswitch`
3. `sudo systemctl enable --now gxw-sip-autoanswer`（或按原记录恢复 Kamailio）。
4. 检查 5160/40002 监听和原线路入呼；保留失败日志与 UTC 备份，不改 A8-C。
