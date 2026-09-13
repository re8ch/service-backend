# GitHub–Flux cutover runbook

This rollout deliberately creates a second, suspended Flux source before changing
the root source. It prevents an authentication or webhook mistake from stranding
the reconciler that must perform the rollback.

## Secret contracts

Secret values are created outside Git and committed only as SOPS ciphertext.

- `service-backend-github-app-auth`: `githubAppID`, exactly one of
  `githubAppInstallationOwner` or `githubAppInstallationID`, and
  `githubAppPrivateKey`.
- `service-backend-github-webhook`: a random `token` shared only with the GitHub
  repository webhook.
- `service-backend-github-reaction-auth`: `githubAppID`, exactly one of
  `githubAppInstallationOwner` or `githubAppInstallationID`, and
  `githubAppPrivateKey` for the separately installed reaction App. A long-lived
  user PAT is prohibited.

The GitHub App is installed only on `re8ch/service-backend`. Source access needs
Contents read and Metadata read. A separate reaction installation/token may write
Commit statuses, Deployments, Issues, Checks, and pull-request comments; it does
not receive Administration permission.

## Promotion gates

1. Use the Cloud Platform MCP entrypoint to observe the existing GitLab source,
   root Kustomization, controller versions, and current artifact revision.
2. Reconcile the SOPS-encrypted secrets. Unsuspend only the parallel GitHub source
   and canary Kustomization. Verify both resolve the signed GitHub `main` SHA.
3. Expose the notification-controller receiver through the declared Gateway API
   path, configure the GitHub HMAC webhook, and prove that a push shortens
   convergence without granting GitHub runtime administration.
4. Unsuspend the GitHub status Provider and Alert. Evidence must show a Flux
   context on the exact GitHub commit.
5. Change the existing `service-backend` GitRepository URL/provider/secretRef in a
   signed PR. Keep its name stable so every existing Kustomization continues to
   reference the same object.
6. Observe two successful root reconciliations before removing the canary. Retain
   GitLab auth and agent resources for the 24-hour rollback window; remove them in a later
   PR, never in the source-switch PR.

## Rollback

Reapply the last signed manifest that points `GitRepository/service-backend` to
GitLab and `service-backend-flux-auth`, then reconcile the source and root
Kustomization through Cloud Platform MCP. Record the operation ID and both source
revisions in the GitHub migration Issue.
