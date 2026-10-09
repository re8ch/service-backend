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

## Approved Re8ch application scope

The product owner approved AnyCam, CompoCV, Farseer, IncidentRelay and Payments for the Re8ch application organization. Their destination namespaces are `re8ch-anycam`, `re8ch-compocv`, `re8ch-farseer`, `re8ch-incidentrelay` and `re8ch-payments`. The Re8ch Tenant definition bootstraps these five namespace boundaries. Existing workloads, routes, secrets and data remain under their current GitOps authority until each application's cutover is reviewed. An empty destination namespace is not evidence that its application has migrated.

| Application | Current namespace | Destination | Cutover dependency |
| --- | --- | --- | --- |
| AnyCam | `anycam` | `re8ch-anycam` | Model storage, Nuclio function, image pull and device gateway references. |
| CompoCV | `compocv` | `re8ch-compocv` | API relay, Nuclio function, NodePort and public ingress hosts. |
| Farseer | `farseer` | `re8ch-farseer` | Existing `saas.re8ch.com/tenant: farseer` labels and public HTTPRoute. |
| IncidentRelay | `incidentrelay` | `re8ch-incidentrelay` | Database bootstrap, SOPS Secrets, Alertmanager targets and public route. |
| Payments | `payments` | `re8ch-payments` | Hyperswitch database and Redis, SOPS Secrets, payment API and public routes. |

Move one application's desired state and traffic at a time. Verify the destination is Ready under tenant `re8ch`, provision its non-Git secrets, compare its data authority and rollback path, then switch routes. Preserve the existing namespace and data through the rollback window.

Farseer cutover finding (2026-10-09): its stateless web Deployment and Service are Ready in `re8ch-farseer`. The first route move caused intermittent Envoy 503s. Envoy cluster statistics showed connection failures for both tenant Pod IPs and successful requests to both legacy Pod IPs. The tenant controller's default deny ingress policy blocked the gateway path; a dedicated port 8080 ingress policy resolved it. The root Flux Kustomization has `prune: false`, so removing a route from Git does not delete the old live object. The failed move left both same-hostname routes present until the tenant route was explicitly deleted through the platform tool. After policy repair, the tenant canary passed 10 direct public probes. The production host was then added to that route and the old live route explicitly deleted. The old route declaration is removed in this change. The legacy Deployment stays available for the rollback window.

## Other product workloads to assess for Re8ch placement

The live catalog also shows application-shaped workloads outside a `re8ch-*` tenant namespace in `cosyvoice` (1), `cube-trial` (2), `mitos-trial` (4), `observable` (1), `studio-toolkit` (4) and `symphony-trial` (1). These remain candidates, not approved migrations. Confirm the product owner, traffic entry point, data volumes, external identity, image source and current GitOps authority for each before changing placement. The application names alone do not prove that all resources in their namespaces belong to the same organization.

## Shared software: tenant access rather than backend relocation

| Shared backend or service | Re8ch tenant path today | Next review |
| --- | --- | --- |
| Harbor | `registry.harbor.v1` grant and service catalog entry; the legacy human `re8ch-registry` instance is Suspended because its selected identity is not an active Re8ch member. | Create a separate `build-push` tenant service instance without `identityUserRef`. It provisions a project-scoped robot credential for tenant builds and stays independent of human membership. Keep Harbor core under Admin; retain the human instance only for optional browser administration. |
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
