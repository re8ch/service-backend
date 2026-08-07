# Akash Sandbox provider on restricted-node-6dc3f140f9

This directory contains the guarded RE8CH Akash Sandbox experiment. It is
deliberately fail-closed and does not register a provider, create DNS records,
or accept bids unless the operator completes the wallet and rollout gates.

## Fixed boundary

- Chain: `sandbox-2`
- Provider: `provider-sandbox.akash.re8ch.com`
- Ingress: `*.ingress.provider-sandbox.akash.re8ch.com`
- Worker: `restricted-node-6dc3f140f9` only
- Lease pool: 8 CPU, 16 GiB memory, 100 GiB ephemeral storage, 10 pods
- Disabled: GPU, persistent storage, leased IPs and tenant NodePorts
- Public listeners: TCP 80, 443, 8443 and 8444 through the Akash Gateway
- Initial bid policy: reject every order

The provider and its operators may tolerate restricted-node-6dc3f140f9's existing
`dedicated=pg-re8ch:NoSchedule` taint. The taint itself must not be removed.
Lease pods receive the same toleration and a hard restricted-node-6dc3f140f9 affinity from the scoped
admission webhook.

## Files

- `namespaces-and-policy.yaml`: namespaces, quota, limits and network policy.
- `admission.yaml`: fail-closed lease admission service and webhook template.
- `values-*.yaml`: pinned Helm values for NGF, Gateway and Akash components.
- `edge-routes.template.yaml`: existing-Traefik Host/SNI handoff to NGF.
- `publication.env.example`: DNS and MikroTik publication contract.
- `pricing/reject-all.sh`: mandatory initial bid policy.
- `pricing/allow-one-tenant.sh`: bounded, explicitly allowlisted test policy.
- `test-lease.sdl.yaml`: non-privileged one-lease smoke workload.
- `deploy.sh`: gated render/apply workflow; defaults to render-only.
- `verify.sh`: pre/post health, placement, quota and public-port checks.

## Wallet gate

Generate two different wallets interactively. Never paste their mnemonics into
chat, Git, a manifest, or shell arguments:

```bash
provider-services keys add re8ch-akash-sandbox-provider --keyring-backend os
provider-services keys add re8ch-akash-sandbox-tenant --keyring-backend os
```

Export the provider key to a protected local file and keep its passphrase in a
second protected file. `deploy.sh` reads them from files and never commits them:

```bash
umask 077
provider-services keys export re8ch-akash-sandbox-provider > /secure/path/provider-key.pem
```

The provider and tenant addresses are public and safe to pass as environment
variables. The test tenant must differ from the provider owner.

## Render and deploy

Rendering is safe and is the default:

```bash
./deploy.sh render
```

Cluster mutation requires an admin kubeconfig, all nodes Ready, free NodePorts,
existing TLS Secrets, a prebuilt admission image, and an explicit apply mode:

```bash
AKASH_ADMISSION_IMAGE=registry.re8ch.com/ops/akash-lease-admission:<digest-or-tag> \
AKASH_PROVIDER_ADDRESS=akash1... \
AKASH_PROVIDER_KEY_FILE=/secure/path/provider-key.pem \
AKASH_PROVIDER_KEY_PASSWORD_FILE=/secure/path/provider-key-pass.txt \
./deploy.sh apply
```

The deployment remains reject-all. Enabling the one-tenant price policy is a
separate manual action after the provider, TLS, admission and resource tests
pass. Restore reject-all immediately after accepting the single test lease.

## DNS and edge

Do not edit Cloudflare records manually. Add the provider and wildcard records
to the existing in-cluster DNS controller only after the Gateway is healthy.
Both records target the restricted-node-6dc3f140f9 public edge origin. Existing Traefik retains 80/443
and forwards only the Akash Host/SNI to NGF. MikroTik forwards public 8443/8444
directly to the NGF NodePorts declared in `values-ngf.yaml`.

No product API proxies `/v2/`, provider traffic, or Akash tenant traffic.
