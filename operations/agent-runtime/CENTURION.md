# Centurion bounded long-running agent contract

Centurion turns one reviewed GitHub Issue into a sequence of short, disposable
agent sessions with a maximum 24-hour wall-clock budget. It does not keep one
model context, process, Pod or credential alive for 24 hours.

The design follows the useful parts of Anthropic's public long-running-agent
research: initialize a durable plan, make one tractable increment per session,
leave structured artifacts for the next session, and use an evaluator against
environment truth. The RE8CH boundary adds single-writer leases, deterministic
transitions, bounded retries and GitHub-native evidence.

## State and ownership

`initializing → planning → working → evaluating` is the normal path. Evaluation
may return to `working`, pause for a later session, or finish. `failed`,
`cancelled` and `completed` are terminal. An unexpired lease rejects a different
owner; an expired lease permits a new ephemeral runner to resume. Every accepted
transition consumes one session from the declared budget.

The checkpoint is an integrity-sealed, non-secret coordination record. It may
contain Issue identifiers, plans, summaries and evidence URLs. It must never
contain provider keys, GitHub tokens, MCP credentials, cookies, authorization
headers or private keys. Credentials are injected independently into each
approved job and disappear with that job.

## Safety and delivery

- GitHub is coordination authority; Kubernetes is runtime authority.
- ARC is the outer one-job/one-runner boundary. Generated or untrusted code
  still requires the inner OpenSandbox boundary.
- A workflow success is not product or runtime completion. The evaluator must
  record the required repository checks and real-consumer/runtime probes.
- Centurion cannot merge a PR or deploy production in the first slice.
- Kubernetes writes, when separately approved, use the existing bounded cloud
  SSA executor. A local Flux writer remains forbidden.
- Deadline, session count, failure count, idle lease and cancellation are hard
  stops. A corrupt, expired or credential-shaped checkpoint fails closed.

The first smoke test deliberately passes one checkpoint between two independent
ARC jobs. A later observed 24-hour soak is required before claiming continuous
production autonomy.
