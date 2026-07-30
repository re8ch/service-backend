# Vendored Cilium migration artifacts

These artifacts pin the exact pre-release chart used by the RE8CH K3s
Flannel-to-Cilium migration on 2026-07-30.

| File | SHA-256 |
| --- | --- |
| `cilium-1.21.0-dev.tgz` | `5eea9d46a5afb92d5ce8b6456b8288b39159fbfd4c4b71efc8a44116105a6147` |
| `cilium-1.21.0-dev-crds.yaml` | `c15666d64f0bc93d86e1a8f7364f485b2f2cfb7e66ec38d6887887c90f19c891` |

The chart came from Cilium source commit
`82c7f10d7b82b6d04147d90d64f6c3a6be16e8ad`. It is intentionally a
development build because the cluster runs `v1.36.0-rc3+k3s1`. Do not replace
the package or image digests without a separate compatibility review.

Example upgrade:

```bash
helm upgrade cilium ./vendor/cilium-1.21.0-dev.tgz \
  --namespace kube-system \
  -f values-migration.yaml \
  -f values-cutover.yaml
```
