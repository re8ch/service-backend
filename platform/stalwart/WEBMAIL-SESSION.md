# Webmail session encryption

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
