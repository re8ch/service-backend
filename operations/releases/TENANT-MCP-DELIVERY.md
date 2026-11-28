# Tenant MCP delivery

Tenant MCP is published through the reviewed tenant runtime and Agent domain
contracts. GitHub Actions and the bounded cloud SSA executor are the only
platform delivery writer. Provider and ToolHive runtimes perform only their
explicit product operations.

Current completion requires:

1. an immutable signed tenant MCP image and reviewed bundle;
2. the exact protected Environment, OIDC identity and bounded executor RBAC;
3. Cloud SSA accepted/applied state and read-back from the named cluster;
4. Ready runtime workloads and real MCP initialization/tool-call probes;
5. provider bindings resolved for the active organization.

A pushed image, active grant, populated tool catalog or successful apply is not
sufficient on its own. Missing coverage is repaired through the current Cloud
SSA contract.
