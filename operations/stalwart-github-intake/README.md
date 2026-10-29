# CompoCV mail feedback

Reuse the JMAP intake from historical PR #721 and the existing Maintenance App.
A native GitHub Actions schedule runs a bounded scan every 15 minutes; Stalwart
has no native GitHub Issues/Projects bridge, so this small adapter supplies that
missing mapping. No daemon, credential synchronizer or new Kubernetes identity.

Only the protected main workflow runs. It requests a short-lived installation
token for compocv/compocv with Issues and organization Projects write permissions.
It refuses public repositories/Projects. The revocable mailbox app password is
read-only and stored in the approved GitHub environment Secret; the App private
key remains in the existing central Secret. No primary/admin mail credentials.

Set COMPOCV_MAIL_FEEDBACK_ENABLED=true only after provisioning the environment
Secret COMPOCV_STALWART_FEEDBACK_APP_PASSWORD and restricting the environment to
main. Disable with the variable. The self-hosted R640 runner is reused.

GitHub issue markers are the durable idempotency record. Workflow concurrency
serializes all invocations; do not run a second independent consumer. Every scan
reconciles ambiguous issue creation before posting, adds the issue idempotently
to the Project, and only initializes missing fields. It never resets triage.
Keep markers intact. Inbox scanning begins at the pilot's explicit timestamp;
review retention and scan limits before scaling beyond this dedicated mailbox.

Mail is quoted untrusted feedback, never execution authorization. Sender matching
is routing only. HTML-only, truncated and oversized messages are rejected;
attachments are not copied. This first iteration maps each message to an Issue;
reply-to-comment threading and outbound notifications are not yet implemented.
Long observations, medium planning and short experiments use the existing Project
Time horizon field. Default mail intake is short/test/Todo for human triage.
