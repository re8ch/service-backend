# Tenant provider identity and configuration

Harbor and Grafana keep their own users and permissions. `re8ch_identity`
binds each native provider login to an existing Supabase `auth.users` UUID and
an active `core.organization_members` row. The `core.tenant_service_configs`
table stores requested and observed provider configuration and opaque credential
references. Passwords and tokens remain in the Kubernetes credential broker.
The user-facing RPC returns only instances bound to that user's UUID; updating
an existing instance cannot silently transfer its identity binding.

Apply `tenant-provider-config.sql` to `re8ch_identity` as `supabase_admin` after
the core identity migration. Verify its `api.resolve_tenant_service_user` and
`api.upsert_tenant_service_config` RPCs before enabling the reconciler. Provision
`tenant-infrastructure/tenant-provider-identity` through the cluster secret
manager with a `service-role-key` issued by the identity Supabase deployment.
Never commit or print that key. The reconciler deliberately fails closed when
the key, migration, or active user binding is absent.
Keep `tenantPlatform.provider.enabled` false until the migration and Secret
exist, a signed image containing the provider worker is pinned at
`tenantPlatform.provider.image`, and the tenant control plane uses the matching
release. The production HelmRelease declares the provider disabled until that
rollout is ready; merging source code alone does not start a broken Pod.

Before promoting the tenant provider PR, exercise one Harbor and one Grafana
tenant with a real `identityUserRef`: create, redeem, log in, suspend, verify
native access is revoked, resume, and delete. Confirm the identity RPC returns
only that user's active tenant configurations. Inspect `core.product_accounts`
for the provider login mapping and confirm no plaintext provider credential
appears in `core.tenant_service_configs`.
