---
name: platform-operate
description: Perform RE8CH allowlisted Deployment restarts and 0/1 replica changes after explicit user confirmation, using the separate platform operate MCP endpoint.
---

# RE8CH Platform Operate

Use only the `re8ch-platform-operate` MCP server for this workflow.

1. Call the operation-status tool first and identify the exact namespace, Deployment, current replicas, and allowlist status.
2. Before every restart or scale call, tell the user the exact target and effect and obtain explicit confirmation in the current task.
3. Never reuse a previous confirmation for another target or operation.
4. Only request replicas `0` or `1`. Never attempt an arbitrary namespace, Deployment, generic Kubernetes mutation, user administration, database operation, or credential operation.
5. After the confirmed call, re-read operation status and report the result and audit-relevant target. Do not expose tokens or Secret content.
