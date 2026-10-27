# Public repository confidentiality boundary

Audit date: 2026-10-27. Scope: service-backend GitHub configuration and the
NodeContract observer output path. This is not a cluster compromise assessment.

## Effective native configuration observed

| Control | Observed state | Confidentiality limit |
| --- | --- | --- |
| Repository visibility | Public | Source, history and public reports remain readable |
| Secret scanning, push protection, validity checks | Enabled | Do not classify topology or runtime telemetry as credentials |
| Default workflow token | Read; PR approval disabled | Explicit job permissions and App tokens still need review |
| Fork contributor approval | All external contributors | Does not isolate persistent self-hosted runners |
| Observation Environment | Selected branch `main`, type `branch` | Gates Secrets consumption, not logs/artifacts visibility |
| Main ruleset | Active | Reviewed workflows must still avoid secret output |
| Actions policy | All actions allowed; SHA pinning not enforced | Most current actions are pinned, but this is not server-enforced |

Observer, Maintenance and Executor installations have their existing permissions;
none receives Secrets read/write permissions from this change. App installation
permissions were inspected, not expanded. Some release Environments have no
branch protection; their secrets and consumers need a separate scoped audit
before imposing restrictions that could break legitimate release refs.

## Remediation implemented

The observer used to upload per-node conditions and stable references in a
plaintext artifact. Public-repository artifacts are downloadable by signed-in
readers, not only organization members. Opaque identifiers do not make that
runtime information confidential.

New evidence uses authenticated encryption with a dedicated random Environment
Secret, fixed-size padding, and a repository/run-bound envelope. Only ciphertext
crosses the artifact boundary. Governance rejects legacy plaintext, wrong keys,
tampering and mismatched provenance; no raw error is printed. Public Issues now
carry only a review-needed verdict and run ID. Detailed debugging remains through
the authenticated platform interface. See `contracts/infrastructure/README.md`
for custody and rotation.

The new Secret was provisioned via stdin and its metadata read back. Its value
was never written to Git, arguments, logs or a local recovery file. GitHub Secrets
has no plaintext read-back API; end-to-end verification must use the protected
main workflow after merge.

## Remaining work

- Inventory and migrate other public runtime reports, release artifacts and
  static topology sources; this change covers the NodeContract output only.
- Inspect runner isolation separately. A main-only Environment cannot protect
  secrets from a compromised persistent runner that executes its trusted job.
- Review unprotected release Environments against their intended refs and Apps.
- Resolve the remaining unpinned actions before enabling native SHA enforcement.
- After the migration is accepted, separately clean historic plaintext artifacts,
  logs, Issues/Checks and Git history. No historic data is deleted by this change.

References:
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts
- https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments
- https://cryptography.io/en/stable/fernet/
