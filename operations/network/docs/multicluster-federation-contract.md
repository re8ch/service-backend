# Multi-cluster federation contract

This is the single normative contract for RE8CH multi-cluster control and
service collaboration. Other documents may link to it, but must not redefine
these boundaries. `Federation` without a plane qualifier is ambiguous and must
not be used in a design, incident report or acceptance record.

## Four independent planes

| Plane | Authority | Purpose | Explicit non-purpose |
| --- | --- | --- | --- |
| Cluster control federation | The target cluster and its bounded Cloud SSA identity | Deploy and inspect one named cluster through its own API | Service routing, remote-cluster proxying or authorization inferred from AmongCluster |
| Public service federation | PublicEdge and the service's stable HTTPS/protocol endpoint | Default cross-cluster and internal service consumption | Kubernetes ownership or cluster-local discovery |
| Private service federation | AmongCluster, SPIFFE identity and a reviewed export/import contract | Sensitive or explicitly private service exchange | Kubernetes object delivery, kubeconfig sharing or generic remote administration |
| Application federation | The application-owned, versioned API/event/data contract | Split application components across autonomous clusters | Implicit database, filesystem, identity or cluster-network coupling |

No success signal in one plane proves another plane. Never repair one plane by
widening authority or silently rerouting work through another.

## Autonomous cluster control

Every Kubernetes cluster is an independent runtime authority with its own API
endpoint, CA, OIDC audience, protected GitHub Environment, bounded RBAC, Cloud
SSA field ownership, durable release state and failure domain.

The stable endpoint convention is:

- `re8ch`: `https://kube-api.service.re8ch.com`
- `re8ch-atlantic`: `https://kube-api.atlantic.service.re8ch.com`
- future clusters: `https://kube-api.<cluster>.service.re8ch.com`

Temporary names or reviewed raw IPs may be ordered fallback transports only
when their TLS identities and target cluster are unchanged. A ReleaseContract
must name the cluster. Cloud SSA connects directly to that cluster; neither the
main cluster nor AmongCluster proxies the write. Workload ownership remains in
the hosting cluster.

Adding a cluster requires a cluster-specific execution profile, endpoint and
CA, OIDC audience, protected Environment, bounded identity/RBAC and durable
state. Prove dry-run, exact-object apply, read-back and replay against that
cluster before accepting application releases.

## Service federation: public first

Cross-cluster consumers use a stable PublicEdge service endpoint by default.
Internal consumers should use the same endpoint where practical so DNS, TLS,
authentication, policy and protocol behavior are continuously exercised and
do not depend on cluster DNS, PodCIDRs, Nodes or a service mesh.

PublicEdge owns hostname, certificate/listener policy, regional candidate
selection, health-based withdrawal and bounded forwarding. Publishing a service
does not move its workload, data, Secret, Node or SSA ownership.

Use AmongCluster only when a reviewed service contract requires a private path
for sensitivity, compliance or a measured latency constraint. The contract must
name the publisher and consumer SPIFFE identities, protocol, endpoint class,
allowed operations, traffic budget, health/freshness rules and withdrawal
behavior. AmongCluster may exchange those bounded service facts and paths. It
must not exchange kubeconfigs, bearer tokens, Secrets, arbitrary Kubernetes
objects, implicit tenant data or unrestricted proxy access.

Loss of AmongCluster or another cluster must not prevent a cluster from serving
its local endpoints. A private path must fail closed without silently changing
to a broader identity or permission set.

## Application federation

Applications split across clusters through explicit, versioned contracts owned
by the application. Each component must remain independently deployable and
observable. Prefer a public service contract; choose private AmongCluster
transport only after documenting why the public contract is unsuitable.

Shared databases, filesystems, in-cluster DNS and undocumented identity claims
are not federation contracts. Data sharing requires an explicit API,
replication or event protocol with ownership, consistency, retry, idempotency,
schema compatibility, failure and recovery rules.

Authentication is an application dependency, not proof of network federation.
OIDC discovery, authorization, token exchange, account mapping and provider
acceptance are tested separately. A service's mail/data plane and its identity
provider are separate components even when one product supplies both.

### Stalwart reference deployment

Stalwart is deployed and owned in `re8ch-atlantic`. Its approved mail, JMAP,
WebDAV, CalDAV, CardDAV, identity and bounded management surfaces are published
through stable PublicEdge endpoints as their service contracts permit. Main
cluster tools consume those endpoints; they do not acquire Atlantic Kubernetes
ownership. AmongCluster is optional for a specifically reviewed private
management or sensitive exchange path, not a prerequisite for ordinary use.
Stalwart mail/account behavior and the Stalwart IDP integration are configured
and accepted independently.

## Grant semantics and mandatory acceptance

`Grant Active` means only that an authorization intent was accepted by its
controller. It does not prove that the target, identity, RBAC projection,
transport, peer session, service stream or application works. It is never
sufficient completion evidence.

A change is accepted only when every applicable item is recorded:

1. The intended cluster and its cluster-specific endpoint were selected.
2. The token was accepted by that cluster with the expected issuer, audience
   and subject.
3. Exact `SubjectAccessReview` or equivalent authorization succeeded for the
   required object and verb; wildcard or cluster-admin access is not evidence.
4. Cloud SSA dry-run/apply, accepted state, managed fields and read-back came
   from the same target cluster.
5. When AmongCluster is used, both SPIFFE peers authenticated, the session is
   Ready, heartbeat/freshness is within policy and the named service stream
   opens using the bounded grant.
6. DNS, TLS and protocol health pass through the actual PublicEdge endpoint
   when PublicEdge is used.
7. An end-to-end functional probe succeeds from the real consumer identity and
   path, including provider/account acceptance where applicable.
8. Controllers and agents show no sustained timeout, reconnect loop or denied
   projection during the observation window.

## Failure ownership

Classify before changing anything:

| Symptom | Owning plane |
| --- | --- |
| API endpoint, OIDC audience, SSA, RBAC or read-back failure | Cluster control |
| DNS, certificate, listener, regional route or public protocol failure | PublicEdge |
| SPIFFE peer, heartbeat, session, export/import or stream failure | AmongCluster private service |
| OAuth discovery, scope, token, account mapping or provider rejection | Application identity/provider |
| Schema, retry, consistency or component-contract failure | Application federation |

Evidence from another plane may help correlate an incident but may not close
it. Fix the owner and repeat the end-to-end probe.

## Prohibited designs and recoveries

- One default Kubernetes API or credential for all clusters.
- Using the main API endpoint for Atlantic or treating Atlantic as part of the
  main cluster.
- Routing Cloud SSA or generic Kubernetes administration through AmongCluster.
- Treating a grant, registration, Ready Pod or successful login as end-to-end
  service health.
- Widening RBAC, issuing cluster-admin, changing tenant identity or adding a
  wildcard grant to compensate for endpoint, transport or peer-session failure.
- Depending on cluster-local DNS or addresses when an approved PublicEdge
  contract exists.
- Implicitly sharing databases, filesystems, Secrets or identity state between
  clusters.
- Moving a workload to another cluster merely to avoid implementing its service
  or application contract.

Kubernetes remains runtime authority. Reviewed GitHub contracts and immutable
artifacts coordinate delivery; the bounded Cloud SSA executor is the selected
write path. AmongCluster and PublicEdge begin at the service boundary after the
target cluster has accepted the workload.
