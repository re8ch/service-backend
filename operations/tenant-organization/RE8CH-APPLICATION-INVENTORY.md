# Re8ch application organization inventory

Snapshot: 2026-10-09 00:10 UTC. Sources: live `get_platform_service_catalog`, the authenticated `Re8ch Review Demo` tenant capability and service-instance APIs, and `platform/birfucate-metering/release.yaml`. This is a placement review, not a command to move live workloads. The platform catalog reports 99 namespaces and 364 workloads, but 52 namespaces have unclassified configuration authority; its default `application-data` label alone does not establish organization ownership.

## Organization boundary

`Re8ch` uses the Re8ch Admin organization for shared system infrastructure. `Re8ch Review Demo` authenticates to the separate Re8ch application organization (`tenantRef: re8ch`). Organization memberships and user IDs are separate. Shared backends can remain physically operated by Admin while Re8ch owns tenant-scoped service instances, grants, credentials, data and application workloads. Do not copy an Admin identity into a Re8ch service request.

## Already under the Re8ch tenant controller

| Tenant namespace | Visible application workloads | Related older namespace | Cutover review |
| --- | ---: | --- | --- |
| `re8ch-anysite` | 3 | `anysite` | Compare traffic, database and GitOps authority before retiring older objects. |
| `re8ch-dataspace` | 1 | `dataspace` | Compare `storage.re8ch.com` routing and persistent data. |
| `re8ch-ledger` | 6 | `ledger` | Compare function routes and ledger data dependencies. |
| `re8ch-neo4j` | 3 | `neo4j` | Compare graph storage and client routes. |
| `re8ch-ontology-microscope` | 6 | none with the same name | Confirm application ownership and its embedded data services. |
| `re8ch-secretary` | 6 | none with the same name | Confirm application ownership and delivery routes. |
| `re8ch-default` | 0 | n/a | Tenant service and claim control namespace. |

The older and tenant namespaces both contain workload objects for AnySite, Dataspace, Ledger and Neo4j. This inventory does not establish which copy currently receives traffic or owns data.

## Product workloads to assess for Re8ch placement

The live catalog shows application-shaped workloads outside a `re8ch-*` tenant namespace in `anycam` (4), `compocv` (3), `farseer` (1), `incidentrelay` (3), `payments` (8), `cosyvoice` (1), `cube-trial` (2), `mitos-trial` (4), `observable` (1), `studio-toolkit` (4) and `symphony-trial` (1). These are candidates, not approved migrations. Confirm the product owner, traffic entry point, data volumes, external identity, image source and current GitOps authority for each before changing placement. The application names alone do not prove that all resources in their namespaces belong to the same organization.

## Shared software: tenant access rather than backend relocation

| Shared backend or service | Re8ch tenant path today | Next review |
| --- | --- | --- |
| Harbor | `registry.harbor.v1` grant and service catalog entry; `re8ch-registry` is Suspended because its selected identity is not an active Re8ch member. | Select an active Re8ch organization service owner, then plan provider reconciliation. Keep Harbor core and registry infrastructure under Admin. |
| Grafana | `observability.grafana.v1` grant and service catalog entry; no Ready Re8ch instance. The failed request using an Admin identity was soft-deleted. | Select an active Re8ch owner and provision the `app`/`kube` viewer instance. Tenant MCP Ready-instance support is proposed in PR #1638. Keep shared Grafana and metrics storage under Admin. |
| PostgreSQL, Neo4j, Supabase, LiteLLM, OpenSandbox, Nuclio | Platform catalog exposes shared runtime or status, but the Review Demo service catalog currently publishes no equivalent general service instance for most of these. | Define tenant-scoped data/access contracts and grants before exposing application operations. Do not move a shared control plane into a tenant namespace to make a tool appear. |

The Re8ch tenant has nine active consumable grants, including database, model gateway and sandbox grants. A grant is permission, not proof of a Ready provider or a published tenant tool.

## Keep in Re8ch Admin

Cluster and shared control services stay in Admin: Kubernetes and Flux controllers, Cilium/network routing, Rook/Ceph, Dex and auth control, ToolHive registry, Headlamp, Harbor core, Grafana and observability storage, shared database routing, and platform operators. Their application-facing access should be represented as Re8ch tenant resources when a reviewed contract exists.

## Migration order

1. Verify traffic and data authority for the four already duplicated application pairs; retire older placement only through its owning GitOps declaration.
2. Restore Harbor and Grafana tenant instances with an active Re8ch organization identity and verify their tenant-scoped tools.
3. Pick one remaining product application per PR, record dependencies and cutover checks, then move its desired state to a Re8ch tenant namespace.
4. Publish bounded tenant tools for additional shared software only after its tenant authorization and provider contract are reviewable.
