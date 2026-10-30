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
