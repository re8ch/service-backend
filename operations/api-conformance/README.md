# API conformance system

This controller runs bounded Schemathesis checks from reviewed
`APIConformanceTarget` declarations. Discovery never authorizes execution. A
target must pin its OpenAPI SHA-256, allowlisted hosts, operation IDs, canary
tenant, and cleanup contract before mutation testing is accepted.

The runner has no Kubernetes service-account token. It emits one sanitized JSON
line; the controller records the latest status on `APIConformanceRun` and sends
the result to the owning repository as a Check, workflow artifact, and one
rolling regression issue. Response bodies, headers, credentials, and replay
commands are never persisted.

GitHub publication is disabled fail-closed until the SOPS-managed Secret
`api-conformance-github-app` exists with `app-id` and `private-key.pem`. Each
target supplies its non-secret installation ID. The App needs Metadata and
Contents read plus Actions, Checks, and Issues write; it must not receive
Contents write or Administration.

The pilot App is `5307513`, installation `171153917`, scoped to
`re8ch/cloud-functions`. The controller independently pins that mapping and
requests repository-scoped installation tokens with explicit permissions. It
verifies repository installation and commit identity before publication and
rejects GitHub API redirects. Discovery remains opt-in (`DISCOVERY_ENABLED`)
and is disabled until its outbound policy and onboarding review are complete.

`encrypt_app_secret.py` imports an owner-readable local key through an in-memory
pipe into SOPS. It never writes decrypted YAML, prints keys, or overwrites an
existing encrypted output. The GitOps Secret lives under `clusters/re8ch/secrets`.
Installing this Secret does not activate any suspended target or imply that the
OIDC result proxy, short-lived service credentials, or canary fixtures are ready.

The result proxy pins the GitHub OIDC `workflow_sha` of the product repository's
default-branch result workflow in `result-runtime.yaml`. A `workflow_dispatch`
run uses the current default-branch commit, even when its report refers to an
older tested image commit. Rotate this pin in the same GitOps rollout as a
product-branch advance; otherwise redemption fails closed and the service
repository receives neither a Check nor an artifact. Confirm the exact
`cloud-functions/master` SHA before changing the pin.

Production mutation targets use only `schemathesis-canary*` tenants. Financial
fixtures must compensate with immutable reversal entries; they never delete
ledger history or call a real payment provider.

Every new run snapshots publication repository, branch, installation, commit,
image and schema digest in immutable `spec.publication`. Manual requests must
include the exact reviewed snapshot; legacy runs without it cannot publish.
The controller strictly projects runner JSON before persisting or publishing it.
Changing a target while a run finishes cannot redirect that run's publication.

`RESULT_DELIVERY_MODE=oidc` prepares a one-hour `status.delivery` envelope and
dispatches only `run_id`. `RESULT_REPOSITORY_BINDINGS` is a reviewed mapping from
repository to immutable `repositoryId`/`ownerId`; missing entries fail closed.
The default remains `dispatch` until the OIDC receiver and proxy are deployed.
Delivery initialization occurs only while completing a Running run, never when
retrying publication; a consumed claim must never be reset. Failed publication
is recorded on the run and requires inspection rather than blind redispatch.

Run creation and Job submission are not a transaction. The controller retains
the target/global concurrency lock for Pending scheduled runs and Starting
runs, persisting Starting before submitting a Job. A lost response cannot cause
another mutation after restart. Completed Jobs in these phases are collected
normally; an orphan without a Job stays locked for explicit operational review
rather than being silently retried. This intentionally favors safety over
availability when submission state is uncertain.

The result proxy is a system workload in `auth`, reusing its existing kubelet
Harbor pull reference without mounting that credential in the container. Its
ServiceAccount has only named Run GET and Run status PATCH in
`api-conformance-system`, with no Secret/business-data access. The legacy
`POD_NAMESPACE` setting denotes that Run storage namespace here.
The public route is only `/api-conformance/v1/reports` on the existing API
Gateways. GitHub issuer egress is fixed. The receiver workflow SHA is pinned:
review and update it when the cloud-functions master revision changes, before
dispatching reports. Do not weaken the pin or allow arbitrary workflow refs.

The initial identity, payment/Credits, and Commerce targets are committed in a
suspended state. This is intentional: the product schema commit must be merged
and deployed, the repository-scoped GitHub App installation must exist, and a
dedicated canary credential plus reviewed cleanup/reversal fixture must be
provisioned before a target may be enabled. Merely changing `suspend` is not
enough for either mutation target: validation still rejects generated writes
until `allowGeneratedMutations` and a cleanup contract are reviewed together.
