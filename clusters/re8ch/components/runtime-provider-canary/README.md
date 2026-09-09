# Runtime provider canary

This private canary qualifies the provider-neutral invocation and fallback
contract before any real cloud Serverless, Wasm, AI model, tenant, or PublicEdge
integration is allowed.

The gateway tries `local-container` first and the in-cluster
`external-serverless-simulator` second. Sending
`X-Re8ch-Canary-Fail-Primary: true` deliberately bypasses the primary provider
so the fallback path can be verified without disrupting another workload.

After Flux reconciliation, run the smoke test from an approved in-cluster
diagnostic client:

```sh
curl -fsS -X POST \
  http://runtime-provider-gateway.runtime-provider-canary.svc.cluster.local:8080/invoke/hello \
  -H 'content-type: application/json' -d '{}'
curl -fsS -X POST \
  http://runtime-provider-gateway.runtime-provider-canary.svc.cluster.local:8080/invoke/hello \
  -H 'content-type: application/json' \
  -H 'X-Re8ch-Canary-Fail-Primary: true' -d '{}'
curl -fsS \
  http://runtime-provider-gateway.runtime-provider-canary.svc.cluster.local:8080/metrics
```

Acceptance requires the first call to select `local-container`, the second to
select `external-serverless-simulator`, and the fallback counter to increase.
There is intentionally no `HTTPRoute`, public DNS, credential, external egress,
or billable cloud call in this phase.

Rollback is removal of this component from `clusters/re8ch/kustomization.yaml`.
The namespace contains no persistent data.

