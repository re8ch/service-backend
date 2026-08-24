---
name: platform-observe
description: Inspect RE8CH platform inventory, node and workload status, fixed metrics, log summaries, registry metadata, service health, and security posture through the read-only observe MCP endpoint.
---

# RE8CH Platform Observe

Use only the `re8ch-platform-observe` MCP server for this workflow.

1. Start with the smallest inventory or health tool that answers the question.
2. Use fixed diagnostic and summary tools; never infer access to raw Secret data, kubeconfig, arbitrary SQL, or a generic Kubernetes API.
3. Treat every tool as read-only. If the requested outcome needs a mutation, explain that it belongs in the separate operate or admin plugin.
4. State the namespace, service, time window, and evidence behind operational conclusions.
5. Never request, print, or persist tokens, passwords, Secret values, or kubeconfig content.
