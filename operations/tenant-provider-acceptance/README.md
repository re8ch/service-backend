# Live tenant provider acceptance

Run `python operations/tenant-provider-acceptance/run.py --preflight` without
credentials. It checks Harbor's configured auth mode and registry challenge,
Grafana's database health, identity PostgREST, and the tenant MCP auth
challenge. It also queries every authoritative `service.re8ch.com` nameserver
and probes each returned Grafana and tenant MCP address with the correct TLS
hostname. This catches an unhealthy address hidden by a healthy local resolver.
The preflight runner needs `dig` and `curl`. A failed check is a deployment blocker.

The full run creates real Harbor and Grafana tenant service instances, redeems
one-time human credentials, calls each provider's native API, pushes and pulls
a tiny image with the Harbor user's Docker CLI credentials, logs in to Grafana
with a browser session cookie, suspends and
verifies access revocation, resumes and verifies recovery, then soft-deletes and
checks native access is revoked again.
It then creates a replacement instance with the same provider login, verifies
that the replacement credentials work, and deletes it. This catches old
terminal instances that continue revoking new access.
At each lifecycle phase it also reads `my_tenant_service_configs` as the
actual Supabase user and checks that the tenant configuration is visible without
private identity fields.
The Harbor CLI step leaves a uniquely tagged small artifact in the test
project so its remote result remains inspectable. The runner needs a working
Docker daemon; its temporary Docker credential configuration is deleted after
the run. Use a dedicated test project with an artifact retention policy.
It fails rather than skipping a missing input. Use a test tenant with an active
Supabase organization member and a pre-existing Grafana tenant Org/datasource.

Required environment variables: `TENANT_ACCESS_TOKEN`,
`IDENTITY_SERVICE_KEY`, `IDENTITY_USER_TOKEN` (the same user's GoTrue access
token), `IDENTITY_USER_REF`, `TENANT_REF`, `HARBOR_PROJECT`,
`GRAFANA_DATASOURCE_UID`, `GRAFANA_ORG_ID`, and `ALLOW_LIVE_MUTATION=1`.
Supply secrets through the approved secret manager; do not put them in a shell
command, Git, CI logs, or issue comments. The script prints no credential
material. Run it with `python operations/tenant-provider-acceptance/run.py`.

If the run fails after creating an instance, leave its resource reference in
the output for investigation. Do not report the integration as available until
both providers complete the full run and the identity config RPC and live
schema migration are present.
