# Open OAuth runtime application authority

`pg-identity` is the authority for OAuth client identity, exact redirect URIs,
secret hashes, lifecycle, and the owning organization membership. An application
registration is a runtime database write through authenticated RPCs. It does not
create a Git branch, manifest, Flux object, or other per-application GitOps state.

This slice does not publish an `open.re8ch.com` route. That route and its UI/API
adapter can be delivered separately after the identity contract is deployed and
verified. Catalog publication, selectable audience organizations, installations,
service bindings, and usage/metering remain pg-billing/Bifurcate authority rather
than being duplicated in pg-identity.

## Authenticated contract

- `api.create_oauth_application(...)` derives the owner from the caller's selected
  active organization. The caller cannot supply an organization override. Personal
  organizations use exactly the same path.
- Redirect URIs are unique, exact HTTPS values. Wildcards, fragments, whitespace,
  and insecure HTTP values are rejected.
- Public clients are the default, require PKCE, use token authentication method
  `none`, and never receive a secret.
- Confidential clients receive a random secret only in the successful initial
  response. Only its bcrypt hash is persisted. Replaying the same organization
  operation key returns the existing client without returning the secret again.
  Reusing that key with different normalized input is rejected.
- `api.my_oauth_applications()` lists only the selected active organization.
- `api.set_oauth_application_lifecycle(...)` and
  `api.rotate_oauth_application_secret(...)` are available to active organization
  owners/admins and to the member who created the application. Rotation is limited
  to active confidential clients and requires an operation key. A durable rotation
  record prevents a same-key retry from rotating again or returning the secret
  again. If the original response is lost, the caller must intentionally choose a
  new operation key and accept another rotation; secrets cannot be recovered.
- Suspended and deleted clients are disabled in `auth.oauth_clients`; activation
  clears the suspended disable marker. Deleted is terminal; restoring a deleted
  application requires a separate reviewed contract that is not part of this API.

The tables have RLS enabled and no direct authenticated DML grants. The bounded
security-definer RPCs re-derive current identity, active organization membership,
and management rights for every operation. `service_role` does not receive these
user-facing RPC grants.

## Rollout and verification

The reviewed migration requires the existing organization runtime context tables
and `api.current_user_id()`. Apply it through the normal signed GitOps pull request
and Flux path, then verify from both the Job and the database. Before exposing a
public route, exercise public and confidential registration, one-time secret
semantics, Personal Org ownership, revoked membership, lifecycle transitions, and
cross-organization denial. Never include returned client secrets in logs or audit
events.
