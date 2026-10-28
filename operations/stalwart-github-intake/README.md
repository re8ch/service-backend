# Stalwart mail intake (not activated)

The pilot routes mail from `harry@re8ch.com` addressed to the existing
`service-backend-maintain@phonaid.com` account into private
`re8ch/service-backend` Issues. GitHub remains the task authority.

This is the intake component, not a complete autonomous worker. It creates
triage Issues without executable labels, never executes email text, and does
not send email. From-address matching is not authentication. Automatic dispatch
requires a separate verified sender admission path and GitHub task authorization.

The implementation uses Stalwart's declared JMAP session endpoint and
[RFC 8621](https://www.rfc-editor.org/rfc/rfc8621.html) Mailbox/get, Email/query,
and Email/get. HTTPS verification is required; authenticated redirects and
cross-origin API URLs are refused. Use the canonical session URL if discovery
redirects. The mailbox is not modified. Plain-text messages only; oversized,
truncated, or HTML-only messages remain in Stalwart and report rejection.
Attachments are not uploaded. Each message currently creates a separate Issue;
thread replies are not yet mapped to Issue comments.

## Runtime contract

Run one scheduled consumer with a persistent SQLite database on local storage.
All consumers for a mailbox must share that database; independent state stores
are unsupported. The scan starts at the explicit `since` timestamp and checks
all inbox pages on every run. Start with a dedicated pilot inbox volume and
bound retention before scaling to many mailboxes.

Mount a **revocable application password**, never the Stalwart recovery admin
or primary password, and provide its filename as `STALWART_PASSWORD_FILE`.
Verify the existing provisioner's credential type before reusing its Secret.
Mount a repository-scoped, short-lived GitHub App installation token as
`GITHUB_TOKEN_FILE`; the token broker must refresh it before each invocation.
Required GitHub access is Metadata read and Issues write. No credentials in
arguments, images, Git, or logs.

```sh
python operations/stalwart-github-intake/intake.py \
  --config operations/stalwart-github-intake/pilot.json \
  --state /var/lib/stalwart-github-intake/deliveries.sqlite
```

SQLite serialization and a GitHub Issue marker reconcile retries after ambiguous
POST responses. Keep the Issue's first marker intact. A removed marker, deleted
Issue, or independently running state store defeats duplicate detection. Exit 1
requires a bounded scheduler retry; the component does not yet implement a DLQ.

## Activation evidence still required

- Verify mailbox/application-password readiness and repository owner approval.
- Verify Stalwart HTTPS/session access from the actual worker runtime.
- Supply the scoped App token refresh mechanism and durable scheduler state.
- Send one explicit test message from Harry; confirm one Issue after two scans.
- Wire GitHub-approved tasks to the existing sandbox execution API; demonstrate
  signed PR creation, CI, and result mail delivery with the same task ID.

No Deployment, CronJob, or Flux resource is added by this component. Deploy only
after the above runtime inputs are resolved through the existing GitOps path.
