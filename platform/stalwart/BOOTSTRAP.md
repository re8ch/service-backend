# Scoped OAuth and management credential bootstrap

`credentials.stalwart.ensure` is a platform administrator operation. It is not a
mailbox provisioning API and is not yet evidence of a deployed capability. Only
use it after the real v1 provider advertises mail, calendars and files scopes.

The platform delivery supplies `STALWART_BOOTSTRAP_CONFIG`, an immutable reviewed
JSON file containing `origin` (fixed HTTPS provider origin), `domainId` (verified
existing provider domain ID), `revision` (reviewed delivery revision), and
`tokenFile` (server-side mounted platform bootstrap bearer credential). Values
come from authenticated platform inspection and broker provisioning, never chat
arguments. Do not mount that credential into tenant-mcp or the management runtime.
The bootstrap credential must be able to query/create the fixed account and OAuth
client and create/read a credential on the dedicated account. Native cross-account
ApiKey operations additionally require `impersonate`; the bootstrap authority
must be permitted to grant the seven runtime permissions below. This temporary
authority is never copied to the runtime key. Existing reviewed
platform administrative authority performs that one-time provisioning; this does
not authorize reusing recovery credentials as a runtime management token.

The operation reuses the Cloud Platform credential provisioning pattern: fixed
Secret destination, server-side native API calls and stdin-only Secret writes.
It returns an opaque reference and sanitized status. It never returns the native
ApiKey creation response, token, Secret data, raw provider errors or subprocess
output. HTTPS redirects are disabled. Provider schema/capability failures stop
provisioning; no older API or unscoped user-token fallback exists.

## Reviewed registration

- OAuth client `re8ch-self-service`: `firstParty:false`, `scopes` containing only
  mail, calendars and files; `grantTypes` containing only deviceCode;
  `redirectUris:{}`; no client secret. It is a public device client. The current
  tenant connector does not send client_secret or refresh tokens.
- Management account `re8ch-self-service-management` in the configured domain,
  with User role, no group memberships and Replace permissions. A conflicting
  existing account/client is rejected rather than modified or adopted.
- Separate 30-day API key with Replace permissions: authenticate plus
  sysAccountGet/Query/Update and sysDomainGet/Query/Update. No mail, calendar,
  files, OAuthClient, ApiKey-management or superuser privileges. Native source
  confirms ApiKey secrets use Bearer authentication. Account/Domain updates
  remain constrained by the existing reviewed-plan management adapter.
- The new key is stored only at
  `kubernetes://ai-operations/stalwart-self-service-admin`, key `token`, matching
  the reviewed management runtime mount. Provider origin, account/key IDs and
  expiry are non-secret annotations. Runtime origin ConfigMap and approved-plan
  ConfigMap are separate reviewed deployment inputs; this operation does not
  activate tenant collaboration or change grants.

## Execution and recovery

1. Publish the reviewed operation through the existing Cloud Platform image and
   authenticated platform catalog. Provision the reviewed server-side config and
   short-lived bootstrap credential mount. Do not assume source merge publishes it.
2. Call `credentials.stalwart.ensure` with `{"apply":false}` through
   `platform_tool_call`, using its discovered schema. Review the fixed account,
   domain, scopes, permissions, 30-day expiry and Secret destination.
3. Apply with `{"apply":true,"planHash":"RETURNED_HASH"}` from the same
   administrator session. The hash binds the subject, reviewed configuration and
   observed existing objects. A Secret reservation is created before provider
   mutations; concurrent calls cannot issue duplicate keys.
4. Verify Ready, the non-secret Secret reference, separate runtime mount and
   management tool read behavior. Remove the temporary bootstrap mount/authority
   after successful provisioning through reviewed delivery.
5. Any interrupted/unknown native write or failed Secret save leaves the fixed
   reservation in Provisioning. Stop: do not automatically delete it and retry.
   An authorized operator inspects provider object IDs, revokes any orphan API
   key and reconciles the reservation through the platform's reviewed recovery
   path. Existing Ready keys are preserved; expiry requires explicit rotation work.

Only after provider/client/grant/runtime checks pass should the user complete
`collaboration_connection` device consent. Root presents the actual returned
verification URI/code once; agents never authorize on the user's behalf. Follow
[the read-only acceptance runbook](SELF-SERVICE-ACCEPTANCE.md), without applying
RSVP or sending messages. The collaboration grant must target the verified active
Re8ch user organization, never the similarly named Admin organization.

## Pinned native contract evidence

The v1.0.1 [schema](https://github.com/stalwartlabs/stalwart/blob/v1.0.1/resources/schema/schema.json.gz)
defines OAuthClient scope/grant sets and ApiKey Replace permissions. The
[authentication implementation](https://github.com/stalwartlabs/stalwart/blob/v1.0.1/crates/common/src/auth/authentication.rs)
parses API keys as Bearer credentials and requires authenticate permission. The
[credential mapping](https://github.com/stalwartlabs/stalwart/blob/v1.0.1/crates/jmap/src/registry/mapping/account.rs)
creates an API key under the request accountId and returns its secret only upon
creation. Mock tests establish software handling; actual v1 acceptance is pending.

## Deployment ownership and lifetime

The source runtime declares optional read-only `0440` projections. Absence keeps
bootstrap unavailable without blocking the Cloud Platform pod:

| Object in ai-operations | Owner | Lifetime |
| --- | --- | --- |
| ConfigMap `stalwart-bootstrap-config`, key `config.json` | Signed platform delivery | Provisioning window only |
| Secret `stalwart-bootstrap-authority`, key `token` | Platform Credential Broker | Short-lived authority; revoke and remove after verified provisioning |
| Secret `stalwart-self-service-admin`, key `token` | This bootstrap operation / platform broker | Dedicated 30-day runtime key; explicit rotation before expiry |

The config's `tokenFile` must equal `/etc/stalwart-bootstrap/credential/token`;
it cannot select another mounted credential. The optional projection does not
create any Secret or populate authority. After success, remove the bootstrap
config and Secret through the reviewed platform path, revoke its native authority,
and disable bootstrap publication. The distinct management key remains mounted.
A later explicit rotation requires its own reviewed plan; deleting a Ready Secret
is not a supported rotation procedure.

## Obtaining temporary authority without a user-supplied token

`credentials.stalwart.authority.ensure` closes the initial token dependency. Its
only arguments are `action` (`seed` or `revoke`), `apply` and `planHash`. It runs
under the same platform administrator authorization as the existing credential
provisioner. It does not ask the user to paste or generate a token.

After migration has completed, all v1 integrity gates have passed, and the
reviewed HTTPS origin is serving the migrated store:

1. Deliver `stalwart-bootstrap-config/config.json` with the observed existing
   domain ID, exact origin, signed revision and fixed tokenFile path. The token
   file can be absent initially. Enable the two exact bootstrap tools temporarily
   in the protected platform catalog. This is not ordinary tenant publication.
2. Plan `credentials.stalwart.authority.ensure` with `{"action":"seed"}`. It
   reads only the fixed `mail-system/stalwart-recovery-admin` Secret and its
   `STALWART_RECOVERY_ADMIN` key inside the platform process, matching the existing
   mailbox-provisioner's source. Basic authentication is built in memory and sent
   over same-origin HTTPS with redirects disabled. No credential appears in tool
   arguments, subprocess argv, plan, output or logs. The source Secret is never
   modified, copied into the bootstrap Secret, or mounted into the management pod.
3. Apply that exact plan. It creates `re8ch-self-service-provisioner` and a
   provider-generated one-hour API key at the fixed bootstrap Secret. Permissions
   are Replace: authenticate, impersonate, Account Create/Get/Query, ApiKey
   Create/Get, OAuthClient Create/Get/Query, plus the bounded runtime management
   permissions. Account/Domain Update are required because v1 refuses to grant a
   permission absent from the caller's effective permission set. There is no
   superuser, arbitrary object writer, account deletion, or API-key deletion
   permission on this temporary key. The platform-controlled recovery authority
   performs its later revocation; the key cannot broaden its own permission set.
4. Wait for Kubernetes to project that Secret, then run the separate
   `credentials.stalwart.ensure` plan/apply workflow above. It uses the temporary
   bearer and stores a different 30-day management key.
5. Plan/apply `credentials.stalwart.authority.ensure` with `action:"revoke"`.
   It requires the distinct runtime Secret to report the expected owner, origin
   and Ready state. Using the controlled recovery authority, it destroys only the
   recorded temporary native API key, then replaces its Secret with a Revoked
   receipt containing no token bytes. Provider or broker uncertainty stops the
   operation; there is no automatic native-write replay.
6. Remove the optional bootstrap config/receipt and disable bootstrap publication
   through reviewed delivery. The dedicated management account/key is untouched.
   Do not automatically renew the one-hour authority; a new window requires
   explicit operator review, including any orphan keys after an interrupted seed.

These steps do not run on the old v0.16 store, during cold backup, on the isolated
IdP instance, or before cutover validation. The native v1 recovery Basic path is
verified in `common/src/auth/authentication.rs`; API key creation and permission
subset enforcement are verified in the pinned mapping/permissions source. This
implements the existing platform provisioning trust boundary, rather than adding
an external credential exchange or treating recovery credentials as an AI token.

Revocation writes a `Revoking` reservation before native deletion. If its result
is lost, a later explicit revoke plan reads the recorded key. Only an exact native
`notFound` result permits a new cleanup-only plan to erase the Secret bytes;
that plan never repeats native deletion. If the key still exists or the identity
receipt changed, it stops for operator recovery. This is a bounded recovery path,
not automatic retry of an unknown write.
