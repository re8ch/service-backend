# GitHub-native Agent webhook MVP

The MVP uses one organization webhook for ordinary Agent/Automation events.
Repository webhooks are exceptions only, such as the existing Flux push hook.
There is no shared event model, queue or new controller.

The declaration contains only environment variable names. Payload URLs,
webhook secrets and GitHub credentials never belong in Git.

Plan and inspect effective repository exceptions:

```console
RE8CH_AUTOMATION_WEBHOOK_URL=https://example.invalid/github \
  python operations/github-webhooks/reconcile.py
```

Apply after the Automation has supplied its real webhook URL and the approved
credential system has supplied the secret:

```console
RE8CH_AUTOMATION_WEBHOOK_URL=https://automation.example/github \
RE8CH_AUTOMATION_WEBHOOK_SECRET='provided-out-of-band' \
  python operations/github-webhooks/reconcile.py --apply
```

Apply creates or updates the matching organization hook, reads the effective
state back and fails if drift remains. It never deletes hooks. The executing
GitHub App or short-lived token needs organization `Webhooks: write`; listing
the retained repository exceptions needs repository `Webhooks: read`.

The first live read on 2026-11-15 found the retained
`re8ch/service-backend` push webhook active but returning HTTP 503. This MVP
does not replace it; Flux delivery health must be repaired in its owning path.
