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

This evidence advances the domain from `inventory` to `shadow`. It does not satisfy server-side dry-run, takeover, idempotent replay, drift recovery, rollback, or the 24-hour stability gate. Flux/controller removal remains prohibited.

