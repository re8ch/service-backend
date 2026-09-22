# Controlled subclusters

Controlled subclusters are independent Kubernetes clusters whose owners explicitly
delegate API access to another organisation. The model does not federate workloads,
Services, Secrets, storage or tenant data.

## Trust boundary

Each relationship has two independently applied halves:

1. The owner cluster configures an accepted OIDC issuer and installs an owner-reviewed
   RBAC grant. The owner remains able to revoke the grant without access to this
   repository.
2. The consuming cluster stores only discovery material: cluster name, API endpoint,
   CA certificate and OIDC client settings. It must not store a bearer token, client
   certificate, private key or remote ServiceAccount token.

The two halves deliberately have no automatic cross-cluster reconciliation. A failed
or revoked owner-side grant must fail closed. API access is the only implied
relationship; network peering, service discovery and data-plane access are separate
decisions.

## Identity contract

The current Headlamp integration authenticates through `https://dex.service.re8ch.com` and
Kubernetes observes usernames with the `oidc:` prefix. Until every target API server
is verified to use one stable claim, owner RBAC may list both the verified email and
GitHub login subjects. Those aliases grant access to one human and must be documented
as such.

Owner administrators should prefer a dedicated delegated group for routine platform
operations. A human `cluster-admin` binding is reserved for the cluster owner or an
explicitly approved administrator. Do not infer Kubernetes access from GitHub
repository membership.

## Onboarding sequence

1. Record the proposed relationship in `inventory/platform/controlled-subclusters.yaml`.
2. Observe the target API server's OIDC flags and the authenticated username/groups.
3. The owner reviews and applies its local RBAC declaration through its own change
   process.
4. Add the credential-free Headlamp kubeconfig template on the consuming side.
5. Verify `kubectl auth can-i` using impersonation for each declared subject, then
   verify an actual fresh OIDC login.
6. Mark the relationship active only after both sides record evidence.

Revocation is owner-first: remove the remote RBAC binding, verify denial, then remove
the consuming-side discovery entry. Emergency access must not depend on the remote
organisation's identity provider.

## Owner-side examples

The manifests in `owner-examples/` are examples to be copied into and owned by the
target cluster. This directory is intentionally absent from every Kustomization in
this repository.
