# Stalwart management adapter

The existing Cloud Platform backend publishes `stalwart.admin.read`,
`stalwart.admin.plan` and `stalwart.admin.apply` only to its administrator
identity. The Re8ch tenant gateway must separately allowlist those names in its
native backend. The adapter uses the same management JMAP capability as the
official Stalwart CLI; it does not expose a shell or native-method passthrough.

## Supported operations

- Read Account and Domain metadata, paginated at a maximum of 50 objects.
  Account quota/usage summaries are included; credentials, roles, permissions,
  mail contents and server secret configuration are omitted by an explicit
  output projection, even if the provider ignores requested properties.
- Plan Account descriptions and bounded storage/object quotas, or Domain
  descriptions. Individual quota entries are patched without deleting other
  configured limits. Zero/unlimited quotas and disk limits below current usage
  are rejected. No account creation, membership, role, credential or global
  security changes are accepted.
- Apply only the exact installed, unexpired plan selected by its SHA-256 hash.
  Native `ifInState` prevents an intervening change from being overwritten.
  Readback verifies the requested fields; already satisfied plans perform no
  write. Unknown outcomes never automatically replay a write.

The plan explicitly reports `nativeValidation: deferred_to_provider_on_apply`.
Local schema validation is not falsely reported as a native dry-run. Stalwart
performs native validation for the conditional JMAP set operation.

## Deployment inputs

Provision through the existing credential and signed release paths:

| Variable | Contract |
| --- | --- |
| `STALWART_ADMIN_ORIGIN` | One fixed HTTPS provider origin, no user info, path, query or fragment |
| `STALWART_ADMIN_TOKEN_FILE` | Read-only mounted dedicated management API token; never an MCP argument |
| `STALWART_APPROVED_PLANS_DIR` | Read-only directory of plans installed by the existing reviewed delivery pipeline |

The management token requires authentication and Account/Domain get/query,
plus Account/Domain update if apply is enabled. It does not need create,
delete, credential, role, user mail or global configuration permissions.
Use the deployment's approved credential system to provision and rotate it;
do not reuse `stalwart-recovery-admin`, put tokens in Git, or issue tokens
through these tools. Revocation must fail subsequent requests closed.

No changes to those mounts, NetworkPolicies, registry allowlists, or token
provisioning are implied by this source change. The release owner must wire and
verify them in the authoritative Agent GitOps release before claiming the
tools are operational. Do not add broad Secret read privileges to the tenant
gateway to make this backend work.

## Reviewed plans

`stalwart.admin.plan` returns `plan`, `planHash`, sanitized `before`, and
`decision: awaiting_gitops_review`. The proposed plan grants no write authority.
Review the exact plan in a signed PR. Existing cloud delivery verifies the
approved source revision and installs a ConfigMap containing a file named
`<planHash>.json` under `STALWART_APPROVED_PLANS_DIR`:

```json
{
  "approvedRevision": "<40-character reviewed Git commit SHA>",
  "expiresAt": 1793404800,
  "plan": {"...": "exact plan returned by the tool"}
}
```

The example is illustrative and deliberately not a valid executable plan.
`expiresAt` is a Unix timestamp selected during review, normally a short
maintenance window. The `plan` includes its version, origin, authenticated
backend subject, object ID, current provider state and bounded patches.
Its canonical sorted compact JSON SHA-256 must match the requested filename.
The `approvedRevision` field is provenance, not proof of approval by itself:
the read-only mount and existing signed delivery pipeline are the authority.
Never expose a tenant-writable mount or API for installing approved plans.

The backend subject may be the authenticated gateway service identity when the
call traverses the existing tenant gateway. That gateway is responsible for
its current platform-admin membership check; this adapter does not mistake a
caller-supplied subject or tenant name for that identity. An approval for a
direct administrator subject cannot be replayed by another backend subject.

Call `stalwart.admin.apply` with only `planHash`. The adapter reads installed
approval before provider access, checks origin/subject/expiry/hash, checks
current state, then sends one conditional update and reads the result. A
`stateMismatch` requires a new plan and review. A network failure after send
returns `outcome_unknown`; read the target first. A later replay of the same
plan is a no-op if the exact desired fields are already present.

Each completed operation returns `operationId`. Structured logs contain only
operation ID, tool name, hashed subject reference, plan hash, outcome and fixed
error code. Descriptions, credential data and provider error text are withheld.

## Compatibility and acceptance

This adapter verifies the session advertises `urn:stalwart:jmap`, restricts its
`apiUrl` to `/jmap` on the configured origin and refuses redirects. Unsupported
servers return `STALWART_UNSUPPORTED`. Git currently declares Stalwart
`v0.16.18`; this source implementation does not assert its live management API
matches current online documentation. Verify a non-production account/domain
read and one approved description change before production activation.

Acceptance must cover ordinary-member denial both in discovery and direct
calls, rejected arbitrary methods/targets/patches, token-error redaction,
cross-subject and expired plans, concurrent state conflict, quota preservation,
write readback, duplicate invocation and unknown outcome without replay.
Passing mocked tests is not runtime or deployment evidence.

Official references:

- https://stalw.art/docs/management/cli/
- https://stalw.art/docs/ref/object/account/
- https://stalw.art/docs/ref/object/domain/
- https://www.rfc-editor.org/rfc/rfc8620#section-5.3

## Runtime mount provisioning

The owned `platform/cloud-platform/runtime.yaml` projects optional read-only
mounts. Before publishing the admin allowlist, platform-authorized credential
provisioning must create `ai-operations/stalwart-self-service-admin` Secret with
key `token` using a dedicated Account/Domain management identity. Provision
`stalwart-admin-runtime` ConfigMap key `origin` with the reviewed HTTPS origin;
changing that environment value requires a reviewed rollout. Neither value is
accepted from callers. Recovery-administrator credentials must not be reused.

Reviewed signed delivery owns `stalwart-admin-approved-plans` ConfigMap in the
same namespace. Its data keys are approved `<planHash>.json` records described
above. Start with an empty ConfigMap; no plan is approved by adding the mount.
Do not grant tenant-api access to this ConfigMap or Secret. The optional sources
allow an unconfigured deployment to remain healthy while management calls fail
closed. The token and plan mounts have mode 0440, are read-only, and avoid subPath
so credential rotation and approved-plan expiration/removal project normally.
Never create these objects through an unreviewed local credential path.
