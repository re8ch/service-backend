# PublicEdge and authoritative DNS recovery runbook

Use this runbook after reading the dated
[multi-cluster federation contract](multicluster-federation-contract.md). Commands are examples;
confirm namespaces, object names, and current repository contracts first.

## Safety and authority checks

1. Read `operations/network/manifests/network/public-mesh/inventory.json` and
   its `r640.publicAuthority` before touching R640 public routes or listeners.
2. Confirm the desired-state change belongs in
   `pillars-infra/network-gitops`, not this coordination repository.
3. Use a clean task worktree from current GitHub `main`, signed commits, checks,
   and a PR.
4. Use the Cloud Platform path first for cluster writes. Local SSH is a bounded
   fallback, not a parallel authority.
5. Preserve evidence before an emergency write. Never store credentials or
   decrypted SOPS data in logs or Git.

## Diagnose in dependency order

### 1. Authoritative DNS delegation and transport

Test every delegated authority directly. Do not start with a recursive resolver.

```bash
dig +time=2 +tries=1 @AUTHORITY_IP kube-api.service.re8ch.com A
dig +tcp +time=2 +tries=1 @AUTHORITY_IP kube-api.service.re8ch.com A
dig +time=2 +tries=1 @AUTHORITY_IP service.re8ch.com SOA
```

Verify UDP and TCP 53, SOA serial, AA flag, answer set, TTL, and latency from CN,
US West, EU central, EU north, and SEA. A secondary returning an answer proves
only that it has zone data; it does not prove that custom GeoDNS ran.

### 2. Recursive view

```bash
dig kube-api.service.re8ch.com A
dig @1.1.1.1 kube-api.service.re8ch.com A
dig @8.8.8.8 kube-api.service.re8ch.com A
```

Compare answer and TTL with the direct authority results. Allow for legitimate
cache retention. If ECS is absent, classification commonly reflects the
recursive resolver rather than the end device.

### 3. Public listener and service protocol

For Kubernetes API, verify TLS and an API endpoint through every returned IP:

```bash
curl --resolve kube-api.service.re8ch.com:443:CANDIDATE_IP \
  --connect-timeout 5 --max-time 10 \
  https://kube-api.service.re8ch.com/readyz
```

Use protocol-specific checks for OAuth token exchange, Headlamp API proxying,
PostgreSQL, SMTP/IMAP, AnyTLS, and SubAccess. HTTP 200 on one hostname cannot
stand in for them.

### 4. PublicEdge scheduling and endpoints

```bash
kubectl -n regional-routing get daemonset public-edge-manager -o wide
kubectl -n regional-routing get pods -o wide
kubectl -n regional-routing get endpoints,endpointslices
kubectl -n regional-routing describe daemonset public-edge-manager
```

The desired replica count must not depend on external authority health. Check
for terminating pods still present in Service endpoints.

### 5. SSA ownership and accepted state

```bash
kubectl -n regional-routing get daemonset public-edge-manager -o json \
  | jq '.metadata.managedFields[] | {manager,operation,subresource}'

kubectl -n regional-routing get configmap \
  networking-helm-public-edge-manager-regional-routing-state -o json \
  | jq '.data | {acceptedDigest,appliedDigest,status}'
```

Object names may change; derive the state ConfigMap using the release tooling
rather than guessing if the command does not match. Completion requires the
intended `acceptedDigest` and `appliedDigest` to be identical, with no
unreviewed manager owning networking SSA fields.

### 6. Artifact reachability

Confirm a node can fetch the exact image digest without requiring the failing
PublicEdge path. Inspect containerd mirror configuration and DNS before pulling.
Do not “fix” a digest-pinned release by substituting a tag.

## Current P0 chart defect

PublicEdge 0.8.11 renders multiple node names into one Kubernetes node-name
`matchFields` expression. Kubernetes permits only one value in that expression.
The preferred correction is one node selector term per node name, for example:

```yaml
requiredDuringSchedulingIgnoredDuringExecution:
  nodeSelectorTerms:
    - matchFields:
        - key: metadata.name
          operator: In
          values: [suxunji]
    - matchFields:
        - key: metadata.name
          operator: In
          values: [domain-ex]
```

Terms are ORed; expressions within a term are ANDed. Add a chart test that
renders two names and passes Kubernetes server-side dry-run. Do not replace the
names with the authority-health label.

## Release procedure

1. Fix and test the chart in `public-edge/public-edge-manager`.
2. Merge through a signed PR and publish a new immutable release.
3. Record source commit, image digest, chart digest, and release workflow.
4. Update the minimum references in `pillars-infra/network-gitops`.
5. Merge only after checks pass.
6. Run protected `static-release` using `publish` for the new source or `reuse`
   only with the exact already-published digest and source commit. Set execution
   true. Use force takeover only for objects explicitly authorized by contract.
7. Confirm workflow success, accepted/applied digest equality, SSA ownership,
   rollout, live config, regional DNS answers, and end-to-end service probes.

The failed 0.8.11 bundle is
`sha256:f95491de9cdf278381d8ed765cfd78e9072c88c1a959298f4ccb3da730fda36d`.
Do not reuse it after changing rendered content; publish a new artifact.

## Acceptance matrix

| Area | Expected first choice | DNS UDP/TCP | HTTPS/API | Cross-cluster fallback |
| --- | --- | --- | --- | --- |
| CN | R640/CN | authoritative answer, bounded latency | ready/service-specific success | overseas candidate only when needed |
| US West | LA1/LA2 | LA-ranked answer | success through both | FLK/Helsinki/KL then CN |
| EU central | FLK | FLK-ranked answer | success | Helsinki/LA/KL then CN |
| EU north | Helsinki | Helsinki-ranked answer | success | FLK/LA/KL then CN |
| SEA | KL | KL-ranked answer | success | LA/EU then CN |
| Unknown | healthy default | never empty solely due to unknown geo | success | policy-defined |

For every row, record source location, resolver, direct authority, returned
addresses, TTL, transport, latency, and timestamp.

## Registry bootstrap loop recovery

Symptom: new PublicEdge pods cannot start because containerd redirects GHCR to
`registry.re8ch.com`, while that registry hostname depends on the unavailable
PublicEdge path.

Temporary recovery may pre-pull the exact digest directly on explicitly named
nodes, but this is not the permanent design. The durable fix must provide an
artifact path independent of the workload being rolled out, such as a healthy
direct upstream fallback, a node-local cache, or a registry endpoint outside
the same failure domain. Validate the fix by deleting one disposable pod and
observing a cold pull during a simulated PublicEdge outage.

## Rollback

Roll back only to a known accepted immutable bundle. Confirm its image still
exists on all required architectures and its configuration does not restore the
health-label scheduling lock. After rollback, repeat authoritative UDP/TCP 53
and service-specific probes; a successful Kubernetes rollout is insufficient.

If changing R640 public delivery, use the rollback in
[R640 Cilium Gateway recovery](r640-cilium-gateway-recovery-2026-10-19.md).
