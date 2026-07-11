# Agent Collab Teable Cloud Attempt

This folder records the July 2, 2026 Teable cloud deployment attempt for the
`agent-collab` tenant. It is intentionally documentation-first: the live Teable
Kubernetes workload was rolled back, while the non-running tenant/database/OSS
setup was left in place for future retry or cleanup.

## Do Not Treat This As A Deployment Guide

This document records a failed historical attempt. It is not the current
placement guidance.

Do not place Teable on `vm-20-12-ubuntu` / ICP control plane. On 2026-07-10 a
Teable retry accidentally reused this old placement and overloaded the small
control-plane node during image pull, migration, and startup. K3S apiserver and
kine began returning TLS handshake timeouts and slow SQL warnings. Recovery
required killing the Teable/Redis workload processes, restarting `k3s`, scaling
the Teable Deployments to zero, and deleting the remaining Teable Pods.

The corrected first deployment target is restricted-node-6dc3f140f9, and the current manifest is:

```text
manifests/k3s/agent-collab/teable-pg-re8ch.yaml
```

That manifest must keep Teable off `vm-20-12-ubuntu`.

## Current State

- Namespace/RBAC: `agent-collab` was created through the RE8CH cluster MCP
  tenant tooling.
- Database Secret: `agent-collab/teable-db-credentials` exists in Kubernetes.
- Pigsty database: database `teable` and roles were created on Pigsty.
- OSS: buckets `re8ch-teable-agent-public` and
  `re8ch-teable-agent-private` were created in `cn-hangzhou`.
- RAM: user `teable-agent-collab-oss` and policy
  `TeableAgentCollabOSS` were created for those buckets.
- Runtime workload: Teable/Redis Deployments, Services, ConfigMap, runtime
  Secret, Redis PVCs, and related PVs were deleted during rollback.
- Public routing: no Ingress was kept, and no public DNS was changed.

## Important Findings

- The generated database URLs initially used `pgwrite.zt.re8ch.com:5433`.
  Teable migration failed there with SASL authentication failure.
- The same migration credential worked against
  `pgwrite.zt.re8ch.com:5432/teable`, and Teable successfully applied its
  database migrations on that port.
- `rancher-panel` was too tight for this workload during the attempt. Its
  memory requests were already high, and it temporarily became `NotReady`.
- Moving the draft workload to `vm-20-12-ubuntu` avoided Harbor and Nuclio
  nodes, but the user requested a stop before completing service readiness.
- Teable's image is large, around 624 MB during the pull observed in this
  attempt, so first startup can be slow on fresh nodes.

## Rollback Performed

The following Kubernetes objects were removed from `agent-collab`:

- `Deployment/teable`
- `Deployment/teable-redis`
- `Service/teable`
- `Service/teable-redis`
- `ConfigMap/teable-config`
- `Secret/teable-runtime-secret`
- `PersistentVolumeClaim/teable-redis-data`
- `PersistentVolumeClaim/teable-redis-data-vm20`
- residual Teable Pod and released local-path PVs

Post-rollback checks showed all nodes Ready and no Teable PV output.

## Secrets And Local Files

- Do not commit runtime secrets, database passwords, OSS access keys, or
  generated connection URLs.
- The local Aliyun access key JSON was stored under
  `local/teable-cloud/.aliyun-oss-access-key.json`.
- `local/teable-cloud/.gitignore` ignores everything in that directory except
  the `.gitignore` itself.

## Future Retry Notes

- Prefer a dedicated platform node with at least 4 GiB RAM before retrying.
- Do not place Teable on `harbor-registry` or Nuclio nodes.
- Use Pigsty native PostgreSQL protocol for Teable. PostgREST is optional and
  separate from Teable's own app database.
- Keep Teable migrations in an init container using the migrator database URL.
- Keep public Ingress/DNS as a separate explicit step after service health is
  confirmed.
- Re-check Qwen/API smoke endpoints before any public routing change.
