# Webmail session encryption

## Webmail sign-in

Webmail offers both enterprise SSO and direct JMAP password sign-in. The direct
form accepts a per-account Stalwart application password, including for accounts
whose primary identity is managed by the external OIDC directory. Use the full
mailbox address as the username. Create or revoke application passwords in the
Stalwart account security UI or through an approved, bounded administrative tool;
do not put generated values in Git, logs, or agent command arguments. This does
not create a local primary password for an OIDC-managed account. The primary
password remains with the external identity provider.

The existing `stalwart-cli` image is used by cluster-internal jobs through the
Stalwart management API. Agent access must remain on that authenticated platform
path with narrow permissions and reviewed writes; do not copy the recovery
administrator credential onto an agent workstation.

Bulwark needs `SESSION_SECRET` to encrypt the server-side Stalwart authentication
context used by account security operations. Without it, SSO can load mail while
`POST /api/auth/stalwart-context` returns 500 (`SESSION_SECRET not configured`)
and application-password operations return 401 (`Not authenticated`).

Before deploying the Secret reference, provision `mail-system/stalwart-webmail-session`
through the approved platform credential workflow. Its `session-secret` key must
contain a cryptographically random secret of at least 32 characters (recommended:
32 random bytes encoded as 64 hexadecimal characters). This is a Webmail server
encryption key, not a mailbox password. Never put its value in Git, PRs or logs.
Retain it in the approved credential system, keep it stable across Pod restarts,
and restrict access to the Webmail workload and credential operators.

Deploy only after the Secret exists. The non-optional reference deliberately
prevents a new Pod from starting without the key. After the reviewed deployment,
sign in through SSO again, confirm the authentication-context request succeeds,
and verify application-password management before configuring IMAP/SMTP clients.
Test mailbox authentication separately from TLS reachability; IMAP uses
`mail.re8ch.com:993` and SMTP submission uses `mail.re8ch.com:465`, both with TLS.

Rotate the key on suspected compromise or according to the credential system's
rotation policy. Coordinate the Secret update with a Webmail rollout because the
key is read from the environment. Rotation invalidates encrypted Webmail sessions;
users must sign in again. It does not rotate Stalwart mailbox application passwords.

## Bounded administrator provisioning

The Cloud Platform tool `credentials.webmail-session.ensure` is restricted to
platform administrators and the exact Secret named above. Call with `apply=false`
to review the target, then `apply=true` to generate 32 random bytes server-side.
It accepts no credential value, namespace, or Secret name. Existing nonempty keys
are preserved; an existing Secret missing the key is rejected rather than rotated.
Concurrent creates preserve the winner. Results contain metadata only. This tool
requires the reviewed Cloud Platform release to be deployed before discovery.

The generic `kube.resource.apply` interface intentionally rejects inline Secret
values. Its Credential Broker message does not imply a supported content-reference
upload API. Tenant Secret tools apply only to tenant-owned Ready namespaces and
must not be used to reassign `mail-system` to a tenant.
