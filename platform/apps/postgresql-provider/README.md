# PostgreSQL provider

Independent MCP and lifecycle provider for `database.postgresql.shared.v1`.
It provisions tenant-owned databases and owner roles inside the existing Pigsty
`pg-re8ch` authority. `pg-identity`, `pg-billing`, and `pg-re8ch` remain
protected Admin authorities and are never created, suspended, or deleted by a
tenant request.

The provider reads its Pigsty administrator credential inside the cluster,
connects with the PostgreSQL protocol, and stores generated tenant credentials
only in `credential-broker`. MCP, service-instance status, bindings, logs, and
usage events contain references and metadata only.

Tenant lifecycle remains `service_instance_plan/create/suspend/resume/delete`.
The provider watches the resulting `TenantServiceInstance` resources; callers
do not receive a raw SQL, Pigsty, Secret, or Kubernetes execution surface.
