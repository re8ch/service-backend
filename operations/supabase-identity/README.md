# Supabase Auth identity migration

The deployment target is the existing `pg-re8ch` Pigsty cluster, using its
primary Service `pg-re8ch-primary.pigsty-external.svc.cluster.local` and a
separate database named `re8ch_identity`. The
existing RE8CH `auth` database and the existing Supabase `postgres` database
remain in service until their consumers are migrated and verified. Do not
retarget the current Supabase stack by changing only `POSTGRES_DB`: GoTrue,
PostgREST and Storage use separate secret-held DSNs, and Studio/Meta use
`POSTGRES_DB`.

`identity-stack.yaml` is the isolated deployment for GoTrue, PostgREST,
Studio/Meta and Kong. `studio-public-route.yaml` exposes its Kong dashboard at
`https://supabase.re8ch.com`, protected by the dashboard Basic Auth credentials
in `supabase-identity-secrets`. The legacy Supabase stack remains reachable at
`supabase.service.re8ch.com`. Its DNS record points directly to the
inventory-declared public Gateway to avoid Cloudflare browser filtering on
the admin login; the Gateway terminates HTTPS before Kong.
It uses the new `supabase-identity` namespace and Secret, without touching the
existing Supabase stack. Provision `supabase-identity-secrets` with distinct
DSNs for `re8ch_identity` before adding this file to GitOps. The existing
Secret must not be copied verbatim because its DSNs still name `postgres`.
The `supabase-identity-secrets` Secret was provisioned in the cluster with
distinct JWT signing material and all database DSNs targeting `re8ch_identity`.

## GitHub login

GoTrue uses the existing RE8CH GitHub OAuth app directly. The app must have
`https://auth.re8ch.com/auth/v1/callback` as an exact callback URL, alongside
the existing auth-gateway callback. Register that exact callback in the GitHub
OAuth app before promoting this GoTrue deployment; keep wildcard matching off.
After a successful login, remove the retired `db-rw.re8ch.com` callback from
the app. Copy its `GITHUB_OAUTH_CLIENT_ID` and
`GITHUB_OAUTH_CLIENT_SECRET` keys from the `auth/re8ch-auth-gateway-secrets`
Secret into `supabase-identity/supabase-identity-secrets` through the cluster
secret manager before deploying the GoTrue GitHub provider. Never write their
values to Git. Keep `DISABLE_SIGNUP=true`: the imported GitHub provider ID
`61690719` already belongs to the imported `harry@re8ch.com` user, and an
unrecognized GitHub identity must not create an account merely because its
email is verified. The GitHub account's current primary email can differ from
the RE8CH account email; the provider ID is the stable link.

After importing legacy users, run `normalize-imported-user-tokens.sql` as
`supabase_auth_admin`. Legacy rows can leave unused token strings and
`email_change` as NULL; GoTrue v2.196.0 scans them into non-null Go strings
when the account signs in. The migration converts only those NULL values to
empty strings, preserving active token values and password hashes.

Stalwart's OIDC Directory points to `auth.re8ch.com` and validates tokens;
Stalwart does not initiate a GitHub login. Do not put Stalwart upstream of
GoTrue for this browser login, because that reverses the dependency and cannot
authenticate the GitHub account.
GoTrue's outbound OAuth provider calls use the existing node-local
`mihomos-node-cn-local.egress-fabric.svc.cluster.local:17891` service. Its
GitHub authorization-code exchange must not depend on one hardcoded overseas
node address: `192.0.2.29:17893` reset the connection in the October 2
login recovery. TLS verification remains end to end; private and cluster
requests retain the existing NO_PROXY exclusions.

The Auth image is pinned to the verified Harbor mirror of Supabase Auth v2.196.0;
the namespace has a `supabase-identity-harbor-pull` image pull Secret. Rotate its
registry credentials through the cluster secret manager.

## Database and secret preparation

1. Observe Pigsty primary and backup/PITR health. Record schema-only and
   count-only inventories of both source databases. No credential values or
   password hashes may enter Git or logs.
2. Reuse the existing `supabase_admin`, `supabase_auth_admin`,
   `supabase_storage_admin`, `authenticator`, `anon` and `authenticated`
   roles on `pg-re8ch`. From an approved admin session on its primary, run `bootstrap.sql` with
   `psql -X -v ON_ERROR_STOP=1 -d postgres -f bootstrap.sql`. It creates only
   `re8ch_identity` and grants the existing Supabase roles access. Confirm
   the target is empty before first GoTrue startup.
   Connect as `supabase_auth_admin` to `re8ch_identity` and run
   `auth-schema.sql` to create GoTrue's empty `auth` schema under that role.
3. Provision distinct Secret references for the new identity deployment.
   The GoTrue, PostgREST, Studio/Meta and optional Storage connection strings
   must all name `re8ch_identity`. Set the GoTrue DSN `options=-csearch_path%3Dpublic%2Cauth` so its migration ledger resolves in `public` and its user/provider tables resolve in `auth`. Use the existing secret manager; never copy
   or print a live password to build a DSN in a shell argument.
4. Start GoTrue in isolation to initialize its schema. Connect as
   `supabase_auth_admin` and run `auth-reference-grants.sql`, then apply the
   `cloud-functions/sql/025_supabase_identity_core.sql` migration as
   `supabase_admin`. Run importer preflight and freeze writes to the legacy
   auth database for final cutover. As `supabase_admin`, run `import-begin.sql`
   immediately before the transactional importer, and `import-end.sql` after
   reconciliation or on import failure. The temporary policies permit the
   importer to count rows through RLS and are not needed at runtime.
   The reference grants include read-only RLS policies for Studio's
   `supabase_admin` role, so its user and identity views show imported GoTrue
   rows without granting browser clients direct table access.

## Public issuer gate

The Stalwart upstream IdP has the dedicated public hostname
`https://id.re8ch.com`. After its DNS, TLS and discovery issuer are live,
run `stalwart-provider-domain.sql` as `supabase_auth_admin` to update GoTrue's
custom provider and clear its cached discovery metadata. Verify `/auth/v1/authorize`
redirects to `id.re8ch.com` before retrying interactive sign-in.

The approved new issuer is `https://db-rw.re8ch.com/auth/v1`; `auth.re8ch.com`
hosts the static User Hub and its OAuth consent screen at `/`. GoTrue v2.196.0
supports `openid`, `profile`, `email`, `phone` and `offline_access` scopes.
The OAuth server is enabled for the explicitly registered Headlamp public PKCE
client. Its fixed client ID is `ed4e0331-7522-59f8-8a72-2d558844dc24`.
GoTrue keeps `aud=authenticated` on that client's OAuth access token and adds
the client UUID as the separate `client_id` claim. Kubernetes therefore trusts
the `authenticated` audience only when its AuthenticationConfiguration also
validates that exact `client_id`; Headlamp forwards the access token, not the
client-audience ID token, to the API server.
MUBAO's K3s start gate also resolves this issuer through the declared internal
Cilium Gateway because its WSL NAT resolver's public answer is not
hairpin-reachable from that control-plane host.
The `supabase-oauth-clients-headlamp-v2` Job declares its exact callback URL
and uses an empty client-secret hash for GoTrue's public-client row scanner.
Before changing Headlamp's issuer, verify the Job completed and the authorize
endpoint redirects to User Hub with an `authorization_id`.

ConnecTool clients still require `groups` and exact resource audiences. A
reviewed access-token hook and live GitHub team synchronization must restore
these claims before their cutover. Do not infer platform administrator
membership from an organization `admin` role.

Keep Stalwart and ConnecTool on their existing issuers until their token
audience and group contracts are validated. Stalwart's external OIDC directory
can consume Supabase access tokens, but its mailbox email aliases do not by
themselves authenticate another Supabase identity.

Switch HTTPRoute and all client issuer declarations only after migrated user
password login, organization RLS and the entire OAuth client matrix pass.
The cutover deliberately invalidates old sessions. Preserve the legacy auth
database and reviewed old route declarations for rollback. Any production
Studio schema edit must be recorded as SQL and followed by a reviewed schema
migration PR.

## Identity REST and Realtime

The identity data routes add `https://db-rw.re8ch.com/identity/rest/v1`,
`https://db-ro.re8ch.com/identity/rest/v1`, and
`wss://db-rw.re8ch.com/realtime/v1/websocket`. They leave existing `/auth`
and `/auth/v1` routes intact. The read-only PostgREST deployment uses
the dedicated `identity_ro_authenticator` login role with
`default_transaction_read_only=on`. It currently connects to the primary:
the declared replica endpoints do not contain `re8ch_identity`, and the
primary reports no active streaming replicas. The database rejects writes on
this connection even if an API function exposes a write endpoint. Migrate the
connection to a verified replica only after replication and database presence
have been checked. The read-only session cannot issue `LISTEN`; its PostgREST
notification channel is disabled and schema changes require a deployment
restart to reload the cache.

Before merging the Realtime manifests, check pgBackRest status, confirm
`wal_level=logical`, and provision these additional keys in the existing
`supabase-identity-secrets` using the cluster secret manager:

- `PGRST_RO_DB_URI`: dedicated `identity_ro_authenticator` credentials for
  `re8ch_identity` on `pg-re8ch-primary.pigsty-external.svc.cluster.local`.
  Grant `anon`, `authenticated`, and `service_role` to this `NOINHERIT` login
  role, and set `default_transaction_read_only=on` before exposing the route.
- `REALTIME_DB_PASSWORD`: password for dedicated `supabase_realtime` login
  role with replication privilege and ownership of `_realtime`.
- `JWT_PUBLIC_JWKS`: only public EC signing JWKs from `GOTRUE_JWT_KEYS`.
- `JWT_VERIFICATION_JWKS`: those public EC keys plus the existing internal
  HS256 verification JWK, for PostgREST compatibility during key migration.

Never publish either key set as a static file: the verification set contains a
symmetric key. Keep the keys aligned with GoTrue when rotating signers. As
`postgres` against `re8ch_identity`, apply `realtime-bootstrap.sql` before
starting Realtime. The deployment pins the upstream Realtime image digest and
seeds tenant `supabase-identity-realtime`; its Service name must match that
tenant because Realtime derives the tenant from the upstream Host header.
After the service has created `realtime.messages`, apply
`identity-realtime-events.sql` to install the private user channel policy and
profile/authorization invalidation triggers. Clients subscribe to private
`identity:user:<GoTrue UUID>` and refetch from REST after `identity_changed`.
The event is advisory; API authorization always checks the database.

## Google and Apple bridge

GoTrue uses the existing Google and Apple OAuth clients, with their already
registered provider callback URLs at `api.re8ch.com/auth/callback/{provider}`.
The API gateway dispatches a callback with a matching legacy signed state to
the legacy handler and proxies a GoTrue state to `/auth/v1/callback`. This
preserves old clients while permitting GoTrue PKCE login. Copy the provider
client IDs and secrets from `auth/re8ch-auth-gateway-secrets` to the identity
Secret through the cluster secret manager; never put values in Git. Apple’s
client-secret JWT must be renewed before expiry. Keep `DISABLE_SIGNUP=true`.
Separate provider linking domains prevent automatic linking by matching email;
the imported provider subject or explicit account binding decides the user.
`/auth/v2/callback` is the exact allowlisted post-login destination for the
stateless API facade. Enable browser login only after its callback proxy and
identity mapping checks pass.

Verify the WebSocket upgrade from the public edge and from the User Center
environment, another user's topic denial, the read-only route rejecting writes,
and the absence of long-lived replication slot lag. On Realtime failure, remove
its new HTTPRoute and triggers while keeping GoTrue and REST available.

## Auth gateway image signing

The `auth` Harbor project uses Cosign signatures held in the cluster Registry
workflow. Do not use an interactive Sigstore device login for an auth gateway
release. Build and push the immutable image digest, then run a short-lived
Cosign Job on restricted-node-6dc3f140f9 in the `auth` namespace. Mount
`auth/re8ch-auth-gateway-cosign-signing-key` for `cosign.key`,
`cosign.password`, and `cosign.pub`; mount `auth/harbor-auth-admin` as the
container's Docker config. Sign the digest with the private key and
`--tlog-upload=false`, then verify the exact digest with the public key. Keep
the private key and registry credential inside their Kubernetes Secrets; do
not copy them to a workstation, command line, log, or Git. Remove the temporary
Jobs after verification. The resulting signature must be visible as a Harbor
OCI referrer before the signed GitOps image-pin PR is merged.

### PublicEdge identity API calls

Tenant calls shared session and organization APIs at `https://api.re8ch.com/auth/v2`.
Auth Gateway calls GoTrue at `https://db-rw.re8ch.com/auth/v1`, identity RO/RW at
`https://db-ro.re8ch.com/identity/rest/v1` and `https://db-rw.re8ch.com/identity/rest/v1`,
and legacy auth RPCs at the corresponding public `/auth` endpoints. These API
contracts use public DNS names and HTTPS through the existing PublicEdge routes,
with no private service DNS/IP override. Explicit NO_PROXY hosts keep RE8CH API
requests off the external OAuth provider proxy. The managed proxy remains for
provider connectivity (for example GitHub code exchange).

The legacy RPC client previously replaced the shared HTTP client's resolver on
its first request, breaking later internal service-name lookups. The gateway
release isolates that client and reports identity infrastructure failures as 503
instead of forcing an OAuth refresh with a false 401.
