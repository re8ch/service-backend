# Unified CredentialContract architecture

Status: architecture proposal; no runtime implementation authorized  
Date: 2026-10-22  
Scope: RE8CH GitHub organizations, GitHub Actions and Apps, Kubernetes clusters,
runtime credential brokers, observability, and GitHub Projects governance

## 1. Decision

RE8CH should define one public, versioned `CredentialContract` as the authority
for **who may prove an identity, which capability that identity may request,
where it may be exercised, under which approval and freshness constraints, and
how the grant is observed and governed**.

The contract contains no credential. It is a compiler input. One reviewed
contract produces five projections:

1. GitHub Actions OIDC trust policy;
2. Kubernetes authentication, RBAC, ServiceAccount and workload-identity
   bindings;
3. Runtime Credential Broker verification and issuance rules;
4. credential lifecycle, drift and revocation observations;
5. GitHub Issue and Project governance policy.

The important correction to the earlier architecture is:

> A GitHub App can be a Kubernetes observer or executor. It does not gain that
> capability from GitHub repository permissions alone. It gains it when a
> reviewed CredentialContract binds verifiable GitHub evidence to a bounded,
> short-lived Kubernetes identity.

GitHub Actions and GitHub Apps use different evidence. Actions can obtain a
GitHub-issued OIDC token. A GitHub App installation receives an opaque,
short-lived installation token and signed webhooks; GitHub does not issue an
Actions OIDC token merely because the actor is an App bot. The unified contract
therefore supports three proof lanes without pretending they are the same:

- **Actions lane:** GitHub Actions OIDC JWT -> direct Kubernetes JWT
  authentication or Broker exchange;
- **App lane:** App JWT / installation identity plus verified webhook or API
  evidence -> Broker-issued workload JWT, or dispatch of a protected workflow
  that obtains its own Actions OIDC token;
- **Kubernetes lane:** bound ServiceAccount TokenRequest JWT -> Broker or an
  external workload-identity provider.

All three resolve to one logical contract subject and one capability graph.

## 2. Authority and trust model

```text
                 signed main + protected review
                            |
                            v
                  CredentialContract source
                            |
             deterministic compiler + policy checks
         +------------------+---------------------+
         |                  |                     |
         v                  v                     v
  GitHub OIDC trust   Kubernetes auth/RBAC   Broker issuance rules
         |                  |                     |
         +------------------+---------------------+
                            |
                            v
                   short-lived capability
                            |
             +--------------+---------------+
             |                              |
             v                              v
      Kubernetes API                 GitHub API / provider API
             |                              |
             +--------------+---------------+
                            |
                            v
          redacted evidence, Checks, Issue and Project view
```

The authorities remain separate:

| Concern | Authority |
| --- | --- |
| Identity and capability intent | signed `CredentialContract` on protected `main` |
| GitHub workflow identity proof | GitHub Actions OIDC issuer |
| GitHub App identity proof | GitHub App key, installation binding, webhook signature and GitHub API |
| In-cluster workload proof | Kubernetes bound ServiceAccount token |
| Runtime authorization | Kubernetes RBAC and target provider policy |
| Credential issuance decision | Runtime Credential Broker using compiled rules |
| Approval | protected GitHub environment, ruleset and immutable approval evidence |
| Actual runtime state | target Kubernetes or provider API |
| Human work lifecycle | Issue contract; Project is a derived view |
| High-volume telemetry | metrics, logs and traces outside Git |

No generated projection is independently editable. A manual RBAC, trust-policy
or Project-rule change is drift and must be reverted or represented by a source
contract change.

## 3. Canonical contract

The portable repository form is YAML validated by JSON Schema. It is not a
Kubernetes CRD, although the same object can later be served as a CRD if a
runtime controller is justified.

```yaml
apiVersion: identity.re8ch.com/v1alpha1
kind: CredentialContract
metadata:
  name: gitops-observer
  uid: cc_01JAZ4YQ2Y0G4Q9C9K8S0M2N6H
  owner: platform-security
  risk: medium
  labels:
    system: gitops
    service: observer
spec:
  subject:
    name: gitops-observer
    actorClass: automation
    proofAnyOf:
      - type: githubActionsOIDC
        issuer: https://token.actions.githubusercontent.com
        audience: https://broker.re8ch.com
        repository:
          ownerId: "1234567"
          repositoryId: "7654321"
          name: re8ch/service-backend
        workflow:
          ref: re8ch/service-backend/.github/workflows/observe.yml@refs/heads/main
          sha: 0123456789abcdef0123456789abcdef01234567
        environment: observation
        events: [schedule, workflow_dispatch]
        runnerEnvironments: [github-hosted]
      - type: githubAppInstallation
        appId: "5426247"
        installationId: "174122066"
        ownerId: "1234567"
        repositoryIds: ["7654321"]
        webhookEvents: [workflow_run, deployment_status]
      - type: kubernetesServiceAccount
        clusters: [r640]
        namespace: gitops-observer
        name: gitops-observer
        audiences: [https://broker.re8ch.com]
  capabilities:
    - name: observe-flux
      targetRef: kubernetes/r640
      effect: allow
      resources:
        - apiGroups: [source.toolkit.fluxcd.io]
          resources: [gitrepositories, ocirepositories, helmrepositories, helmcharts]
          verbs: [get, list, watch]
        - apiGroups: [kustomize.toolkit.fluxcd.io]
          resources: [kustomizations]
          verbs: [get, list, watch]
        - apiGroups: [helm.toolkit.fluxcd.io]
          resources: [helmreleases]
          verbs: [get, list, watch]
        - apiGroups: [""]
          resources: [events]
          verbs: [get, list, watch]
      namespaces: [flux-system]
      exclusions:
        resources: [secrets]
        verbs: [create, update, patch, delete, deletecollection, impersonate,
                bind, escalate]
  issuance:
    mode: brokerExchange
    tokenFormat: jwt
    audience: kubernetes.r640.re8ch.com
    ttl: 5m
    maxUses: 1
    requireMainHead: true
    requireSignedCommit: true
    requireCurrentInstallation: true
    bindTo:
      - repositoryId
      - workflowSha
      - runId
      - runAttempt
      - capability
      - target
    replayWindow: 0s
  approval:
    mode: none
    reason: read-only, secret-excluding observation
  lifecycle:
    notBefore: 2026-10-22T00:00:00Z
    reviewEvery: 30d
    expiresAt: 2027-01-22T00:00:00Z
    rotationBefore: 14d
    revokeWithin: 15m
  observability:
    availabilitySLO: 99.9
    exchangeLatencyP95: 2s
    maxCredentialAge: 5m
    staleUseAfter: 24h
    issueAfterConsecutiveFailures: 3
    redactClaims: [jti, token, signature]
  governance:
    project:
      owner: re8ch
      number: 1
    dedupeKey: credential-contract/gitops-observer
    requiredReviewers: [platform-security]
    escalation:
      deniedExchangeBurst: p2
      projectionDrift: p2
      expiryWindow: p2
      privilegedScopeChange: p1
```

### 3.1 Required invariants

The schema and compiler reject a contract unless all of these hold:

- `metadata.uid` is immutable and is used in generated subject names; display
  names are never security identifiers.
- GitHub organization and repository numeric IDs are required. Human-readable
  names are audit labels, not the only trust anchors.
- A workflow grant pins `workflow_ref`, `workflow_sha`, allowed event, audience,
  ref/environment and runner class. Repository-only trust is insufficient.
- Every capability names exact targets, API groups, resources, verbs and
  namespaces. Wildcards require `risk: critical`, expiry and explicit approval.
- `secrets`, `tokenreviews`, `subjectaccessreviews`, `serviceaccounts/token`,
  `impersonate`, `bind` and `escalate` are denied unless individually declared.
- Read and mutation capabilities are separate contracts or separate grants with
  separate proof and approval. Read access cannot be upgraded during exchange.
- Issued credentials are short-lived, audience-bound and narrower than both the
  source proof and the contract. The Broker may reduce authority, never expand
  it.
- A contract cannot make its own compiler, ruleset, approval environment,
  Broker root key or Kubernetes authentication configuration less restrictive.
- Projects and Issues never grant runtime authority. They may supply referenced
  approval evidence only when a protected policy explicitly permits it.

## 4. Logical data model

The contract separates concepts that are often accidentally collapsed:

| Object | Meaning | Examples |
| --- | --- | --- |
| Subject | stable logical actor | `gitops-observer`, `release-executor` |
| Proof | evidence about the current caller | Actions OIDC, App installation, KSA JWT |
| Capability | bounded action vocabulary | observe Flux, patch Deployment status |
| Target | exact authority endpoint | Kubernetes cluster/namespace, GitHub repo |
| Grant | subject + proof + capability + target + conditions | scheduled observation on `main` |
| Credential | ephemeral realization of one grant | five-minute Broker JWT |
| Approval | immutable evidence required before issuance | environment deployment approval |
| Observation | redacted fact about grant health or use | exchange denied, RBAC drift |
| Governance item | durable actionable projection | expiry Issue in Project #1 |

This produces a common identity tuple:

```text
(contract_uid, subject, proof_type, proof_subject, capability,
 target, source_revision, approval_ref, issued_at, expires_at)
```

The tuple, not a bearer token, is the durable audit record.

## 5. Compiler architecture

The compiler is deterministic and has no network or secret access. Its inputs
are the contract, target inventory, schema bundle and compiler version. Outputs
are content-addressed and carry source contract UID and digest annotations.

```text
contracts/credentials/*.yaml
inventory/{github,kubernetes,brokers,projects}.yaml
schemas/credential-contract.schema.json
                         |
                         v
                  credential compiler
        +----------+----------+----------+----------+
        |          |          |          |          |
        v          v          v          v          v
      github/    kubernetes/ broker/   observe/   governance/
```

Recommended repository layout:

```text
contracts/
  credentials/
    observer.yaml
    maintenance.yaml
    executor.yaml
  capabilities/
    kubernetes-observe-flux.yaml
    kubernetes-apply-approved.yaml
  targets/
    github-re8ch.yaml
    kubernetes-r640.yaml
generated/credentials/
  github/
  kubernetes/
  broker/
  observability/
  governance/
schemas/
  credential-contract.schema.json
  credential-evidence.schema.json
operations/credential-contract/
  README.md
  compiler.lock
```

Generated files are committed initially for review and drift visibility. The
compiler check fails if regeneration changes the tree. A later OCI bundle may
carry the same projections, but the signed source contract remains portable.

## 6. Projection 1: GitHub Actions OIDC trust

The GitHub projection contains two distinct products:

1. repository/workflow configuration allowing only selected jobs to request an
   OIDC token through `permissions: id-token: write`;
2. Broker or provider trust rules that validate the resulting token.

Trust evaluation requires:

- issuer exactly `https://token.actions.githubusercontent.com`;
- contract-specific audience;
- immutable `repository_id` and `repository_owner_id`;
- exact `workflow_ref` and pinned `workflow_sha` or `job_workflow_sha`;
- allowed `ref`, environment, event, runner environment and reusable workflow;
- bounded token age and unique `jti` replay handling;
- current repository and App installation inventory when required.

The compiler may configure GitHub's organization OIDC subject template to use
stable numeric claims, but the Broker must still validate individual claims;
string formatting of `sub` is not a substitute for claim validation.

Fork pull requests, untrusted reusable workflows and arbitrary refs cannot mint
production or observation credentials. The job that requests an OIDC token has
no GitHub App private key and receives no standing kubeconfig.

## 7. Projection 2: Kubernetes RBAC and workload identity

The Kubernetes projection can use either of two modes.

### 7.1 Direct GitHub OIDC mode

The API server trusts the GitHub issuer through structured
`AuthenticationConfiguration`. CEL validates claims and maps an accepted token
to a reserved RE8CH username and group such as:

```text
username: github-actions:cc_01JAZ4YQ2Y0G4Q9C9K8S0M2N6H:<run_id>
group:    re8ch:credential-contract:cc_01JAZ4YQ2Y0G4Q9C9K8S0M2N6H
```

Generated RoleBindings bind that group to exact read-only roles. This is a
valid low-latency design, but it has meaningful costs: every API server becomes
a GitHub trust verifier, revocation is limited by JWT expiry, approval and
one-time use are harder, and claim-policy changes become API-server security
configuration changes.

### 7.2 Broker-issued Kubernetes JWT mode (recommended)

The API server trusts only the RE8CH Broker issuer. The Broker first verifies
GitHub or Kubernetes proof and then issues a five-minute, target-specific JWT.
Structured authentication maps `contract_uid`, `capability` and `target` claims
to RE8CH groups. Generated Role/ClusterRole and bindings grant only those
groups. This provides one place for replay prevention, revocation, approval
checks and cross-cluster audit.

For in-cluster workloads, the compiler also emits:

- one application-specific ServiceAccount;
- `automountServiceAccountToken: false` by default;
- explicit projected TokenRequest volume only when the workload needs it;
- exact audience and expiration;
- namespace Role/RoleBinding wherever possible;
- ClusterRoleBinding only when cluster-scoped reads are unavoidable;
- NetworkPolicy allowing only Broker, Kubernetes API and required telemetry
  endpoints.

The observer contract does not read Secrets, exec into Pods, proxy nodes, create
Jobs, request arbitrary ServiceAccount tokens or impersonate users.

## 8. Projection 3: Runtime Credential Broker

The Broker is a policy decision and token exchange boundary, not a secret
warehouse and not a general reverse proxy.

### 8.1 Exchange request

```json
{
  "contractUid": "cc_01JAZ4YQ2Y0G4Q9C9K8S0M2N6H",
  "capability": "observe-flux",
  "target": "kubernetes/r640",
  "proofType": "githubActionsOIDC",
  "proof": "<bearer token>",
  "sourceRevision": "<40-character commit>",
  "approvalRef": null,
  "nonce": "<client-generated nonce>"
}
```

The proof is never logged or stored. The response is a short-lived credential
plus public metadata suitable for an exec credential plugin:

```json
{
  "token": "<broker JWT>",
  "expiresAt": "2026-10-22T13:05:00Z",
  "credentialId": "cred_...",
  "contractDigest": "sha256:...",
  "auditRef": "audit://credential-exchange/..."
}
```

### 8.2 App lane

A GitHub App bot has two safe routes:

- **Broker-native:** the Broker verifies an App JWT, current installation,
  repository selection, narrowed installation-token permissions, webhook
  signature and delivery ID. It then issues its own target JWT.
- **Workflow-mediated:** the App dispatches an allowlisted protected workflow.
  That workflow independently acquires Actions OIDC and performs the exchange.

The second route is preferred for mutation because environments, reviewers,
rulesets and workflow SHA are first-class approval evidence. Broker-native App
exchange is appropriate for continuous read-only observation and GitHub event
projection. An installation token is evidence about GitHub API authority; it is
never accepted directly by Kubernetes as a bearer credential.

### 8.3 Decision algorithm

The Broker fails closed in this order:

1. load the exact compiled contract digest;
2. validate proof signature, issuer, audience and time;
3. bind stable IDs and current installation/ServiceAccount inventory;
4. validate source revision, protected ref, workflow and runner class;
5. verify requested capability and target are an exact subset of the grant;
6. verify approval evidence and its binding to revision, environment and run;
7. atomically consume `jti` or delivery ID where single use is required;
8. issue the shortest supported target credential;
9. persist only a redacted audit tuple and outcome.

Retries with the same consumed proof return the prior credential metadata but
never return the credential again. Ambiguous issuance is treated as consumed.

## 9. Projection 4: lifecycle observability

The observability compiler emits recording/alert rules, synthetic checks and a
redacted evidence schema. It observes the **identity path**, not just workload
uptime.

| Signal | Meaning | Action |
| --- | --- | --- |
| contract projection digest mismatch | generated policy drift | block release; deduplicated Issue |
| OIDC discovery/JWKS stale | proof cannot be safely verified | page only before expiry budget is exhausted |
| exchange success/error/deny by contract | availability and attack signal | metric; burst escalation |
| issued TTL exceeds contract | Broker safety violation | P1 and disable contract |
| replay accepted | security boundary failure | P0, revoke Broker issuer/key |
| RBAC can-i differs from compiled matrix | runtime authorization drift | P1/P2 depending on expansion |
| unused grant past `staleUseAfter` | removable privilege | maintenance Issue |
| approaching contract/key expiry | lifecycle risk | Project item before rotation window |
| revocation propagation exceeds SLO | stale authority remains usable | incident |
| App installation/repository selection drift | GitHub proof no longer matches | suspend exchange |

Cardinality is bounded by contract UID, capability, target, outcome and coarse
reason. Repository, workflow and run IDs belong in trace/audit evidence, not
unbounded metric labels. Tokens, signatures, raw claims and webhook bodies are
never telemetry.

The lifecycle state machine is:

```text
Draft -> Validated -> Active -> Rotating -> Active
                   |          |
                   v          v
                Suspended -> Revoked -> Expired
```

Activation requires every mandatory projection to report the same contract
digest. Revocation stops new issuance first, then validates target rejection;
deleting source files before revocation completes is forbidden.

## 10. Projection 5: GitHub Projects governance

Projects is a view over contract and evidence, never an authorization engine.
The compiler emits Issue templates, labels, dedupe rules, field mappings and
transition guards.

Recommended Project fields:

| Field | Source |
| --- | --- |
| Contract UID | immutable contract metadata |
| Subject | contract subject |
| Capability | requested or unhealthy capability |
| Target | target inventory reference |
| Risk | compiler-derived maximum risk |
| Lifecycle | contract lifecycle state |
| Projection health | aggregate of the five projection checks |
| Last successful use | redacted audit index |
| Expires | contract lifecycle |
| Approval class | contract approval mode |
| Revision | source commit and contract digest |

Governance rules:

- routine successful exchanges and reconciliation samples remain telemetry and
  produce no notification;
- repeated denial, drift, stale privilege, expiry windows and revocation SLO
  violations create or update one deduplicated Issue;
- read-only contract renewal may be handled by Maintenance App policy;
- changes adding mutation, Secrets, impersonation, RBAC administration,
  public networking or wildcard scope require protected review;
- Executor may consume only an `Approved` operation whose approval evidence is
  bound to the exact contract digest and revision;
- changing a Project field cannot grant access. The corresponding source
  contract PR must merge before the compiler changes runtime projections.

## 11. Observer, Maintenance and Executor contracts

| Subject | Proof lane | Runtime authority | GitHub authority | Approval |
| --- | --- | --- | --- | --- |
| Observer | App-native Broker exchange or scheduled Actions OIDC | read Flux CRs/status/events and selected workload health; never Secrets | Checks/Security/Deployments read; status evidence write only if separately granted | none for bounded reads |
| Maintenance | Actions OIDC plus Maintenance App installation | normally none; optional read-only evidence retrieval | Issues, PRs, Dependabot and Projects lifecycle | policy for routine maintenance; human for critical paths |
| Executor | protected workflow Actions OIDC | exact approved mutation verbs and targets, short-lived | read approval evidence; publish Check/Deployment result | environment approval bound to SHA and contract digest |

This makes the Observer App genuinely capable of Kubernetes observation without
storing a kubeconfig in GitHub. Its persistent App key remains useful only to
authenticate to GitHub and the Broker; every Kubernetes credential is derived,
short-lived and contract-bounded.

## 12. Static and dynamic boundary

The unified contract statically defines and compiles all authorization intent,
but it does not make runtime facts static.

| Can be compiled | Must remain dynamic |
| --- | --- |
| trusted issuers, audiences and claim rules | current signature/JWKS validity |
| workflow/App/ServiceAccount identity bindings | current App installation and repository selection |
| RBAC roles and bindings | API-server admission and actual authorization result |
| Broker issuance policy and TTL ceiling | replay state, revocation and approval consumption |
| alert rules and SLO thresholds | exchange latency, failures and credential use |
| Project field and transition rules | actionable incident lifecycle |

The design removes standing credentials and duplicated policy, not observation
or reconciliation.

## 13. Security and failure analysis

| Risk | Control |
| --- | --- |
| compromised GitHub App private key | key is GitHub-only evidence; Broker also checks installation, repository, event/delivery and contract; prefer sign-only vault |
| malicious workflow on a trusted repo | exact workflow SHA/ref, protected environment, event and runner claim; no repo-only trust |
| repository or organization rename | numeric owner/repository IDs are security anchors |
| OIDC token replay | audience binding, short TTL, atomic `jti` consumption and one-use mutation grants |
| Broker compromise | issuer key in HSM/sign-only store, short TTL, no stored target credentials, independent audit and rapid trust removal |
| overly broad RBAC | exact generated matrix, deny-list invariants, `kubectl auth can-i` differential checks |
| compiler compromise | pinned compiler digest, reproducible output, two-person review of compiler/schema and signed provenance |
| Project approval spoofing | Project is not authority; approval is a protected environment/ruleset artifact bound to SHA and digest |
| GitHub outage | running cluster continues; existing credentials expire; emergency access uses separate audited break-glass path |
| Broker outage | fail closed for new writes; bounded cached read credentials only if explicitly allowed |
| clock skew | narrow accepted skew, synchronized control-plane time and dedicated alert |
| bootstrap deadlock | Broker issuer/authentication roots use a separately reviewed bootstrap contract and cannot self-modify |

## 14. Rollout

### Phase 0 — contract and compiler shadow

- define schema, inventory and capability vocabulary;
- model Observer, Maintenance and Executor without changing live access;
- compile to a review-only directory and compare with current permissions;
- reject any unexplained privilege expansion.

### Phase 1 — Observer canary

- deploy Broker trust and one read-only cluster role;
- use scheduled Actions OIDC first, then App-native exchange;
- prove Flux/status observation, no Secret access, expiry and revocation;
- publish only redacted Check/Deployment evidence and one quiet weekly summary.

### Phase 2 — Maintenance governance

- compile App permission expectations and Project policy;
- reconcile installation drift and lifecycle Issues;
- remove duplicate hand-maintained mappings after parity.

### Phase 3 — Executor canary

- require protected environment approval bound to commit, artifact and contract
  digest;
- allow one namespaced, reversible operation;
- prove replay rejection, uncertain-result handling and rollback evidence;
- expand only by new reviewed capabilities.

### Phase 4 — direct OIDC evaluation

Use direct GitHub OIDC authentication only for clusters where eliminating a
Broker hop materially improves availability and where API-server trust changes,
revocation limitations and JWKS reachability are accepted. It is an optimization,
not the default trust model.

## 15. Acceptance criteria

The architecture is ready for implementation only when:

- one contract regenerates all five projections deterministically;
- every generated object embeds contract UID, source revision and digest;
- privilege-diff tests prove generated authority is no broader than declared;
- Observer can read required Flux state and is denied Secrets and every write;
- App-native and Actions OIDC proofs resolve to the same logical subject but
  remain distinguishable in audit;
- mutation credentials expire within five minutes and reject replay;
- revocation stops new issuance and is observed within the declared SLO;
- Project changes alone cannot affect authorization;
- Broker, compiler and authentication-root changes cannot self-approve;
- no kubeconfig, App private key, bearer token or decrypted Secret is committed,
  logged or placed in an Issue.

## 16. Official protocol references

- GitHub Actions OIDC claims and subject customization:
  <https://docs.github.com/en/actions/reference/security/oidc>
- GitHub App authentication modes:
  <https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app>
- GitHub App installation token scoping and expiry:
  <https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app>
- Kubernetes JWT/OIDC and structured authentication configuration:
  <https://kubernetes.io/docs/reference/access-authn-authz/authentication/>
- Kubernetes ServiceAccount TokenRequest lifecycle:
  <https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/>
- Kubernetes RBAC least-privilege model:
  <https://kubernetes.io/docs/reference/access-authn-authz/rbac/>

