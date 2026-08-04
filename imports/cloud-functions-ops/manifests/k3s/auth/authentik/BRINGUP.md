# Authentik bring-up contract

Authentik is intentionally scaled to zero. The checked-in Helm values now use
the verified PostgreSQL and Redis topology, but the normal release remains
fail-closed until a one-replica canary passes. PostgREST is not a database
transport and must never be substituted for the PostgreSQL wire endpoint.

## 2026-08-04 canary result

- The dedicated `auth` database and `authentik` role passed `SELECT 1` through
  `pgwrite.zt.re8ch.com:5432`. The HA route on port 5433 failed and remains a
  tracked database-routing issue.
- The password-protected in-cluster Redis is 1/1 Ready, persists on a 1 GiB
  `local-path` PVC, and accepts traffic only from Authentik-labeled Pods.
- Server and worker were started with explicit PostgreSQL overrides because the
  historical Helm-generated Secret still contains a fail-closed placeholder.
- The canary was rolled back to 0/0. On `harbor-registry`, replacement Pods were
  scheduled but kubelet/containerd remained in `ContainerCreating` without a
  sandbox event for more than four minutes, even after reducing the canary to
  server-only. This runtime condition must be repaired before retrying.
- Redis and database preflight evidence were retained. No DNS, public Ingress,
  OAuth provider, or ToolHive external MCPServer was created.

Retry with `server-canary.patch.yaml` and `worker-canary.patch.yaml` only after
the node runtime is healthy. Start the server first, verify both health paths,
then start the worker; roll both back to zero on any deterministic failure.

## Required preconditions

1. Provision a dedicated `auth` database and least-privilege Authentik owner
   role through the reviewed Pigsty database workflow. Do not reuse application
   read/write roles or PostgreSQL superuser credentials.
2. Verify direct PostgreSQL connectivity from the intended `rancher-panel`
   workload path to the approved HA write service. Database migrations must not
   run through PostgREST or a read endpoint.
3. Store the database password only in `auth/authentik-secrets`; never place it
   in Helm values, ConfigMaps, Git or an MCP response.
4. Maintain the reviewed password-protected in-cluster Redis deployment and its
   NetworkPolicy; `redis.enabled=false` refers only to the chart subcomponent.
5. Confirm the wildcard/default Traefik certificate renewal path. The currently
   observed public certificate expires in August 2026; production cannot rely
   on a one-time certificate copy.
6. Bring up one server and one worker, wait for migrations and both readiness
   gates, then verify `/-/health/live/` and `/-/health/ready/` internally.
7. Configure the MCP OAuth provider/resource, scopes, PKCE S256, token audience,
   claim-to-tenant mapping and the production plugin callback. Test missing,
   expired, wrong-issuer, wrong-audience, wrong-scope and cross-tenant tokens.
8. Only after the identity and ToolHive external server gates pass, add DNS and
   the public MCP Ingress. `auth.re8ch.com` and `tools.re8ch.com` should use the
   reviewed public edge; never route back to the unrelated Pigsty nginx origin.

## Current verified network facts

- `auth.re8ch.com` currently resolves to `192.0.2.122`, where Pigsty nginx
  presents a private `CN=pigsty` certificate.
- `tools.re8ch.com` currently has no public DNS record.
- The healthy Shanghai Traefik edge at `api-shanghai79.re8ch.com` presents a
  publicly trusted `*.re8ch.com` certificate and already routes the Authentik
  host to a fail-closed 503 because Authentik has no endpoints.
- The custom `re8ch-auth-gateway` is separate from Authentik. Its missing
  `anchor=true:NoSchedule` toleration was repaired and it is now 1/1 Ready.

Use `manifests/k3s/toolhive/verify-external-publication-readiness.sh` and the
`get_external_publication_readiness` ToolHive tool for ongoing evidence. Neither
checker reads credentials or changes DNS/Ingress state.
