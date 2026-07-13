# MikroTik Rule Publisher

This app runs in K3S on the restricted-node-6dc3f140f9 and publishes RouterOS `.rsc` files for
MikroTik to fetch/import.

It is intentionally not a Clash/sing-box dataplane. It is a small control-plane
translator:

```text
external region/IP sources
  -> Python compiler
  -> RouterOS .rsc address-list and mangle scripts
  -> MikroTik fetch/import
```

## Files

- App server: `apps/mikrotik-rule-publisher/server.py`
- Rule compiler: `tools/mikrotik_rules/build_split_lists.py`
- Rule config: `config/mikrotik-routing/*.yml`
- K3S manifest: `manifests/k3s/ops/mikrotik-rule-publisher/mikrotik-rule-publisher.yaml`

## Deploy

Run from an operator machine that can access the K3S cluster.

Build and push the image outside the cluster:

```bash
docker buildx build --platform linux/amd64 \
  -f apps/mikrotik-rule-publisher/Dockerfile \
  -t registry.re8ch.com/ops/mikrotik-rule-publisher:20260713-007 \
  --push .
```

Apply rule configuration and workloads:

```bash
kubectl -n ops create configmap mikrotik-rule-publisher-config \
  --from-file=direct.yml=config/mikrotik-routing/direct.yml \
  --from-file=proxy.yml=config/mikrotik-routing/proxy.yml \
  --from-file=block.yml=config/mikrotik-routing/block.yml \
  --from-file=domains.yml=config/mikrotik-routing/domains.yml \
  --from-file=services.yml=config/mikrotik-routing/services.yml \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f manifests/k3s/ops/mikrotik-rule-publisher/mikrotik-rule-publisher.yaml
kubectl -n ops rollout status deploy/mikrotik-rule-publisher --timeout=180s
```

## Verify

The Service is NodePort `30089`, intended to be reached from MikroTik through
the restricted-node-6dc3f140f9 LAN address:

```bash
curl -fsS http://192.0.2.169:30089/index.json
curl -fsS http://192.0.2.169:30089/re8ch-address-lists.rsc | head
curl -fsS http://192.0.2.169:30089/re8ch-split-routing-canary.rsc
curl -fsS http://192.0.2.169:30089/manual-kit/README.md
```

## MikroTik Fetch/Import

Manual import:

```routeros
/tool/fetch url="http://192.0.2.169:30089/re8ch-address-lists.rsc" dst-path=re8ch-address-lists.rsc
/import file-name=re8ch-address-lists.rsc
/tool/fetch url="http://192.0.2.169:30089/re8ch-split-routing-canary.rsc" dst-path=re8ch-split-routing-canary.rsc
/import file-name=re8ch-split-routing-canary.rsc
```

Do not import `re8ch-split-routing-employee.rsc` or enable Scheduler until the
canary path has passed.
