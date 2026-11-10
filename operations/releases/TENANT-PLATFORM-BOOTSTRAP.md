# Tenant platform GitHub bootstrap

`tenant-platform-bootstrap.yml` is the one-time GitHub-native creator for the
reviewed `tenant-infrastructure` inventory. It consumes the same immutable,
signed agent-runtime OCI bundle used by the ordinary publisher. It does not
read Secrets, write RBAC, impersonate users, or invoke Flux.

The platform access seed is
`operations/releases/tenant-platform-bootstrap-access.yaml`. A platform
administrator must install that seed and the reviewed AuthenticationConfiguration
change before the first run. This is intentionally not part of a Flux root and
cannot bootstrap its own authority. The admission policy restricts CREATE and
UPDATE to the exact namespace and object names; Kubernetes RBAC alone cannot
restrict CREATE by `resourceNames`.

Create the protected GitHub Environment
`platform-bootstrap-tenant-infrastructure`, require the platform reviewer, and
provision only `KUBE_API_CA_PEM`. Then dispatch the workflow with the signed
agent-runtime bundle digest. Run once with `execute=false`; after the server-side
preflight succeeds, approve the same digest with `execute=true`. The ordinary
agent-runtime publisher remains `requireExisting=true` and takes over subsequent
updates.
