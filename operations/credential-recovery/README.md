# WebMail session credential bootstrap

This one-time administrator Job invokes the fixed, create-only broker introduced
by PR #2498. It uses the existing Cloud Platform Kubernetes identity without
adding privileges or copying credentials. The API tool and this Job execute the
same implementation; no Secret values pass through the caller or logs. It does
not require the Cloud Platform SSH inventory volumes, so it can finish the
WebMail credential recovery while that separate rollout is blocked.

Before execution, require a signed merged revision, successful Cloud Platform
release/finalize checks and its immutable image digest. The manifest deliberately
lives outside continuously reconciled domains. Apply it through the platform
administrator resource API, inspect Job completion and metadata-only output,
then remove the completed Job. Re-running preserves an existing nonempty key;
rotation and arbitrary namespaces/names are not supported.

After creation, reconcile the reviewed WebMail deployment and reauthenticate the
browser session before verifying Stalwart application-password operations.
