# Stalwart module context

## Runtime authority

- `re8ch-atlantic` is the only Kubernetes runtime authority for Stalwart.
- Select the authenticated `re8ch-atlantic` cluster binding explicitly before
  reading Pods, Services, Endpoints, Routes, Events, logs, or managed fields.
- A read from the default `re8ch` context is main-cluster state. Historical,
  projected, or compatibility Stalwart objects there are never evidence of
  Atlantic workload health and must not drive recovery actions.
- `overseas-flk` is an Atlantic worker name. Its absence from the main-cluster
  Node list is expected and does not prove an Atlantic node failure.

## Delivery and network authority

- Kubernetes runtime state is authoritative only in the explicitly named
  cluster. Git holds reviewed desired state; it is not runtime evidence.
- Networking desired-state ownership is `pillars-infra/network-gitops`, with
  Cloud SSA delivery recorded under the historically named
  `operations/flux-federation/authority.yaml`. The local canonical source
  snapshot used by repository validation is
  `clusters/re8ch/components/cilium/gateway-api-canary.yaml`.
- `operations/network/manifests/cilium/gateway-api-canary.yaml` is retained
  migration/bootstrap material. Do not use it to infer current listeners or
  delivery state.
- GitHub Actions and the bounded cloud SSA executor are the only Kubernetes
  delivery writer. Do not restore Flux or use direct local writes.

## Incident evidence order

Every observation must record its `clusterRef` and evidence plane:

1. **Atlantic cluster control:** the `re8ch-atlantic` binding, API transport,
   exact RBAC, and API read succeed.
2. **Atlantic workload:** Stalwart Pod readiness, Service Endpoints, storage,
   and listener behavior are read from `re8ch-atlantic`.
3. **PublicEdge transport:** DNS, TCP, SMTP banner/STARTTLS, SMTPS, IMAPS, and
   certificate behavior are tested from outside Atlantic.
4. **Application delivery:** a real external sender is accepted, the message
   is stored in the intended mailbox, and the user can read it.

Success in one plane never implies success in another. In particular, an
`Accepted` TCPRoute, an open TCP port, a healthy WebMail page, or a running
Stalwart process is not sufficient evidence that inbound mail works.

