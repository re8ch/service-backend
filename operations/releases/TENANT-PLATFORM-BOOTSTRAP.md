# Tenant platform GitHub bootstrap

`tenant-platform-bootstrap.yml` is the one-time GitHub-native creator for two
exact inventories: the reviewed `tenant-infrastructure` workload inventory and
the ToolHive approved-catalog takeover inventory. It consumes the same
immutable, signed agent-runtime OCI bundles used by the ordinary publisher. It
does not read Secrets or impersonate users. The ToolHive target
may update only `Role/agent-runtime-domain-toolhive-system` and create or update
only `ConfigMap/re8ch-approved-tool-catalog`; this is the minimum handoff needed
before the ordinary `requireExisting` publisher can take over.

The platform access seed is
`operations/releases/tenant-platform-bootstrap-access.yaml`. A platform
administrator must install that seed and the reviewed AuthenticationConfiguration
change before the first run. It cannot bootstrap its own authority. The
admission policy restricts CREATE and
UPDATE to the exact namespaces and object names; Kubernetes RBAC alone cannot
restrict CREATE by `resourceNames`. ToolHive Role escalation is restricted by
RBAC to the existing executor Role and by admission to that exact name.

Create the protected GitHub Environment
`platform-bootstrap-tenant-infrastructure`, require the platform reviewer, and
provision only `KUBE_API_CA_PEM`. Then dispatch the workflow with the signed
agent-runtime bundle digest and exact target. Run once with `execute=false`;
after the server-side preflight succeeds, approve the same digest and target
with `execute=true`. The ordinary agent-runtime publisher remains
`requireExisting=true` and takes over subsequent updates.
