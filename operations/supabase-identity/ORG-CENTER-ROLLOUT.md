# User Hub organization authority

The GoTrue identity database owns this authority. Apply
`re8ch/cloud-functions/sql/026_identity_unified_org_authority.sql` only after
`025_supabase_identity_core.sql`, from a reviewed migration job. The User Hub
frontend depends on this migration and must be released afterward. Run the
updated `identity-realtime-events.sql` after the migration so membership and
permission changes invalidate clients' cached identity state.

## Data model

- `core.organizations` contains the hidden `admin` root. Enterprise
  organizations default to that parent. A Personal organization is independent
  until its owner explicitly joins an enterprise; it is never made a child of
  `admin` merely because the owner is an Admin member. The root never appears
  in generic `/auth/v2/me` organization claims.
- Every GoTrue user gets exactly one `personal-<user UUID>` organization and an
  owner membership. The signup trigger and migration backfill cover new and
  existing users.
- `core.organization_members` is the sole authorization table. One row per
  organization and user contains a role and an array of scoped permissions.
  Existing direct and group bindings are expanded to these rows before the
  old grant tables are dropped. Orphan bindings abort the transaction.
- Platform administration requires active `owner` or `admin` membership in
  the `admin` organization. An enterprise `admin` role does not confer it.
  The migration seeds only the verified `harry@re8ch.com` user, and aborts if
  that identity is missing. Later email changes cannot create admins.
- `core.account_requests` stores export and deletion requests, separate from
  authorization. It does not grant any access.

## User Hub API

The browser calls PostgREST at `/identity/rest/v1/rpc/*` with the verified
GoTrue bearer token. The reviewed functions are `identity_center`,
`create_organization`, `set_organization_member`, `admin_directory`,
`request_account_action`, `my_account_requests`, and
`admin_account_requests`. The database checks the caller's user ID and
membership; the admin directory and privileged writes require an AAL2 session.
Only the admin directory can enumerate other users. The regular organization
projection omits the hidden root.

## Request handling

The User Hub submits and tracks requests. The admin view shows open requests.
An export or deletion is complete only after the responsible service owners
have fulfilled it across their product databases and the identity operator has
recorded the outcome. This migration does not automatically export data or
delete a GoTrue user: product data, legal retention, and delivery of the export
need a reviewed workflow before completion can be offered in the UI.

## Rollout checks

1. Verify `harry@re8ch.com` is confirmed in GoTrue and record its UUID.
2. Snapshot `core.organizations`, `core.organization_members`, `core.groups`,
   `core.group_members`, and `core.role_bindings` before migration.
3. Run migration in one transaction; confirm every user has one personal org,
   all former scoped grants appear in `organization_members.permissions`, and
   only the intended UUID is a root owner.
4. Reinstall realtime triggers, reload PostgREST schema caches, and verify
   regular users cannot call admin RPCs or see the root in `/auth/v2/me`.
5. Publish User Hub after the SQL and API checks pass. Keep existing login and
   consent flows available during the rollout.

## Identity profile, verified email, sessions, and avatars

Apply `031_identity_profile_devices.sql` through `037_identity_avatar_jobs.sql`
from the reviewed Cloud Functions repository in numeric order after `030`.
Existing six accounts receive verified contact rows as an explicit legacy
backfill; later OAuth email assertions do not create contact rows. The GoTrue
send-email hook permits OTP only for a verified contact or a pending explicit
email change. Configure the same Standard Webhooks secret in
`supabase-identity-secrets/SEND_EMAIL_HOOK_SECRET` and
`re8ch-auth-gateway-secrets/IDENTITY_SEND_EMAIL_HOOK_SECRET` before enabling the
hook. Copy the identity JWT HMAC key to
`re8ch-auth-gateway-secrets/IDENTITY_PGRST_JWT_SECRET`. The internal
`identity_service` role can call only the reviewed hook and avatar RPCs.

The dedicated `re8ch-avatars-1301339749` COS bucket is public-read. The
avatar worker and gateway receive the bucket write key through namespace-local
Secrets. The worker runs pinned Segmind Tiny-SD on R640 CPU; signup enqueues a
job and generated avatars replace the letter placeholder asynchronously.
Google and Apple avatar choices use their linked provider image when one is
available. Uploads pass through the authenticated gateway, which validates and
re-encodes the JPEG before writing COS and committing the object key.

Rollout order: database functions; gateway image and secrets; worker image and
secrets; GoTrue email configuration; EdgeOne User Hub CSP response header; User
Hub static files. Verify current and future account contact projections,
email OTP, device registration, and avatar retrieval after each layer.
