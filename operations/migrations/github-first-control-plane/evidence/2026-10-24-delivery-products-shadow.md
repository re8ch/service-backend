# Delivery-products independent static authority shadow

Observed on 2026-10-24.

- Repository: `re8ch/delivery-products-gitops` (`repository_id=1431915128`, `owner_id=283958313`).
- Bootstrap PR: `re8ch/delivery-products-gitops#1`, merged as `69783864163fbc22e08409877450eed999134d50`.
- Protected environment: `release-executor-delivery-products`; reviewer `Woomou`, `main` only, administrator bypass disabled, `KUBE_API_CA_PEM` present.
- Shadow workflow: `40073134400` on the exact merged `main` SHA.
- Canary contract: `main-delivery-products-aesthete`, three namespaced objects and no Secret or persistent-storage object.
- Signed and verified bundle: `oci://ghcr.io/re8ch/delivery-products-gitops-release/main-delivery-products-aesthete@sha256:f7bffb0b1c56c0875b3ffd8dd80f6ff0dccfc6faa912dbe973d3c878b8463cad`.
- Workflow execution input was `execute=false`; no Kubernetes write identity was requested.
- Live `Kustomization/flux-system/reconcile-main-legacy-delivery-products` remained Ready, unsuspended, and reconciled at `main@sha1:c4d7eae1b2e0b15d00348cc3f88152ddf7e274e7` during the observation.
- Existing accepted state for the canary records the same three-object inventory under the previous service-backend signer. The independent repository must perform an explicit sequence-2 takeover after this OIDC route change is merged and reconciled.
- An API server-side dry-run with field manager `re8ch-release/main-delivery-products-aesthete` accepted all three objects without conflict. This diagnostic used the local administrative context; the exact independent GitHub OIDC identity denial/allowance proof remains a cutover gate.
- Runtime-gate PR `re8ch/delivery-products-gitops#2` merged as `c93f28e60a32467180b674944ebe7914e5e3053e`. It adds a protected `dry_run=true` path that obtains the exact GitHub OIDC identity, proves bounded allow/deny authorization, and requests Kubernetes server-side dry-run without applying.
- Runtime-gate workflow `40074629617` ran on that exact `main` SHA. Its compile/sign job succeeded and produced verified bundle `oci://ghcr.io/re8ch/delivery-products-gitops-release/main-delivery-products-aesthete@sha256:c36c5a79d103135be1300d9894259cbe28c990dab589d3865e91c38ced1c88b0`; `execute=false`, so the execute job was skipped.
- The exact OIDC authentication gate failed closed with HTTP `401 Unauthorized` before authorization or server-side dry-run. No canary object or accepted-state object was written.
- API server metric `apiserver_authentication_config_controller_last_config_info` reported loaded configuration hash `sha256:d85401ee0d4427112f0e8742930ac997e2674138b1fa49307b546b8c13884a74`. That hash exactly matches the repository authentication configuration before the nine-domain split; current `main` declares `sha256:c35ce0c3a1a2adb33c2bef180fafaaa4e23e4527d63131cc86edf5b81d75c17d`.
- The failed gate is therefore attributed to unapplied control-plane host configuration, not to a relaxed fallback identity. The reviewed `clusters/re8ch/components/ops/dex-dual-issuer-authentication.yaml` must be reconciled through the Cloud Platform/controlled host path, and the exact OIDC gate must pass, before any `execute=true` canary.

This evidence keeps the domain in `shadow`. It does not satisfy the exact-identity server-side dry-run, takeover, idempotent replay, drift recovery, rollback, or the 24-hour stability gate. Flux/controller removal remains prohibited.

