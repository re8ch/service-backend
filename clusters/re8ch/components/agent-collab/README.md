# Agent Collab / R640 AI Platform

## Current R640 Runtime

- Teable 和 Redis 固定在 `r640`。
- 集群内 DNS 统一由 Advanced Fabric DNS `10.43.65.40` 提供；本目录不再
  维护节点专用 DNS 或 kubelet DNS 覆盖。
- `teable-edge-relay.yaml` 在 ICP 节点把 Teable R640 NodePort 转给面板
  Nginx；`teable-panel-nginx.conf` 是面板端配置的受控副本。
- `wechat-content-orchestrator.yaml` 提供 Teable → Kagent → Codex MCP
  编排入口。
- `../llm-gateway/litellm.yaml` 提供 DeepSeek/Kimi 的统一协议、虚拟密钥、
  用量和费用记录；Kagent 与 Codex app-server 均通过该网关调用模型。
- `codex-app-server-litellm.yaml` 固化 Codex app-server 的 LiteLLM
  Responses API 配置和无密钥工作负载定义。
- `kagent-ui-r640.patch.yaml` 修复 Kagent UI 启动窗口和只读 Nginx
  日志目录。
- `scripts/bootstrap-teable-content-operations.sh` 幂等创建运营表和首期
  GnuCash 任务。
