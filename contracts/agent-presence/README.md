# Agent presence and work claims

Every agent that edits RE8CH repositories or cluster resources announces what
it is working on, where every other agent and every human can see it. This
applies in every runtime: Claude Code (cloud or local), ChatGPT/Codex, Cursor,
Gemini and in-cluster agents.

The fact store is GitHub Issues, because GitHub is the only system that every
runtime can reach (cloud sandboxes cannot reach `kube-api.re8ch.com`).

## Model

| Concept | GitHub representation |
|---|---|
| Board | Open Issues labelled `agent-claim` in `re8ch/service-backend` (override with `AGENT_PRESENCE_REPO`) |
| Claim | One Issue whose body holds a fenced `re8ch-claim` JSON block ([schema](claim.schema.json)) |
| Lease | `expires_at` in that block. A claim past `expires_at` is void even if the Issue is still open |
| Heartbeat | Rewriting the block with a new `heartbeat_at` / `expires_at` (editing the body does not notify anyone) |
| Release | Closing the Issue as `completed` with a one-line outcome comment |
| Revoke | Anyone (usually Neo) closes the Issue. The holder sees it at its next heartbeat and stops |
| Mirror | For each `gh:` resource: label `agent:working` and one marker comment on that Issue/PR |

Human view: [open claims](https://github.com/re8ch/service-backend/issues?q=is%3Aissue+is%3Aopen+label%3Aagent-claim).

### Resource keys

| Key | Covers |
|---|---|
| `repo:owner/name` | the whole repository (use sparingly) |
| `gh:owner/name#N` | one Issue or pull request |
| `branch:owner/name@branch` | one branch |
| `path:owner/name:glob` | files, for example `path:re8ch/service-backend:clusters/re8ch/apps/litellm/**` |
| `k8s:cluster/namespace[/kind/name]` | a namespace or one object, for example `k8s:re8ch/agent-system/deployment/runner` |
| `<kind>:<value>` | anything else, compared exactly (for example `dns:re8ch.com`) |

`repo:` overlaps everything in that repository, `path:` keys overlap by literal
prefix, and `k8s:` keys overlap by segment prefix. When you claim a pull
request also claim its head branch and the paths you will touch, since
`gh:` and `path:` keys are not linked automatically.

Do not put secrets, customer data or private Issue titles in a claim. The board
repository is public; refer to private work by key (`gh:pillars-infra/product-prd#6`).

### Agent identity

`agent` is `<runtime>/<stable id>`, for example `claude-cloud/session_01Cpg…`,
`codex/task_…`, `cursor/<background-agent-id>`, `cluster/<namespace>/<serviceaccount>`
or `human/Woomou`. In-cluster agents use the ServiceAccount identity from the
credential-broker design. The GitHub author of the Issue is only the token
owner and is not the agent identity.

### Modes and conflicts

- `exclusive` (default): you will write. Two exclusive claims from different
  agents conflict when any of their keys overlap.
- `shared`: you are reading or observing. Shared claims are visible but never
  conflict.
- **Lowest Issue number wins.** GitHub assigns Issue numbers atomically, so
  every agent computes the same winner without a lock server. After filing,
  wait a few seconds, list the board again, and if an older active claim
  overlaps yours, close yours as `not_planned` with `Yielded to #N`.

## Protocol (every agent, every runtime)

1. **Before editing**, list active claims that overlap what you will touch. If
   one exists, do not edit. Pick other work, or comment on that claim to
   coordinate.
2. **Claim** with a TTL of 30 minutes (max 8h) and a one-line intent. Re-check
   for an older overlapping claim and yield if you lost.
3. **Heartbeat** at least every TTL/2 and before every push, merge, `apply` or
   PR edit. If the claim is closed or expired, stop writing immediately and
   re-claim from step 1.
4. **Release** as soon as the work is handed off (PR opened for review, task
   abandoned, session ending). Put the outcome (PR link) in the comment.
5. A crashed agent's claim lapses at `expires_at`. Anyone may close an expired
   claim; `presence.py` does this on every `claim` and `sweep`.

The pull request remains the final synchronization point. Claims prevent two
agents from working on the same thing before a pull request exists.

## Using the CLI (shell + token)

`operations/agent-presence/presence.py` uses only the Python standard library.
It reads `AGENT_PRESENCE_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` or `gh auth token`
(needs Issues write on the board repository, and on the mirrored repositories
for labels and comments).

```bash
P=operations/agent-presence/presence.py   # or fetch it raw from GitHub
python3 $P list                                      # who is working on what
python3 $P check -r gh:8iops/agent-gitops#230        # exit 3 if busy
python3 $P claim -r gh:8iops/agent-gitops#230 \
  -r branch:8iops/agent-gitops@codex/runner -r path:8iops/agent-gitops:clusters/re8ch/agents/** \
  -i "Add agent-runner Job template" --ttl 30m       # exit 0 granted, 3 conflict
python3 $P heartbeat 3001                            # exit 4 = revoked, stop now
python3 $P release 3001 --note "PR 8iops/agent-gitops#231 ready for review"
```

Set `AGENT_ID` and `AGENT_RUNTIME` when the defaults (derived from the
environment) are not stable across a session.

## Using GitHub tools only (no shell token)

Agents that only have GitHub connector or MCP tools (Claude cloud sessions,
ChatGPT connectors) follow the same protocol with Issue operations on
`re8ch/service-backend`:

1. List open Issues with label `agent-claim`, read each `re8ch-claim` block,
   and ignore those whose `expires_at` has passed.
2. Create an Issue titled `[agent-claim] <agent> → <first key>` with label
   `agent-claim` and this body:

   ````markdown
   <!-- re8ch-agent-presence v1 -->
   **<agent>** is working on this. Close this Issue to revoke the claim.

   ```re8ch-claim
   {"schema": "presence.re8ch.com/claim/v1", "agent": "claude-cloud/<session>",
    "runtime": "claude-cloud", "mode": "exclusive", "intent": "<one line>",
    "resources": ["gh:owner/name#N"], "started_at": "<now UTC>",
    "heartbeat_at": "<now UTC>", "ttl_seconds": 1800, "expires_at": "<now + 30m>"}
   ```
   ````

3. List again. If an older active overlapping claim exists, close yours as
   `not_planned` with `Yielded to #N`.
4. Add the `agent:working` label and a short comment linking the claim to each
   `gh:` target.
5. Heartbeat by updating the body with new times. Release by closing as
   `completed`, and remove `agent:working`.

Runtimes that cannot write GitHub Issues at all must not edit shared resources
directly. They may only open pull requests, which remain visible.

## Tradeoffs and next steps

| | GitHub-native claims (this) | In-cluster lease service |
|---|---|---|
| Reachable from cloud sandboxes | Yes | No, until a public authenticated endpoint exists |
| Human visibility on GitHub | Native | Needs a mirror |
| Atomicity | Issue-number ordering plus a re-check, about 3 s settle | Real compare-and-set (`coordination.k8s.io/Lease`) |
| Latency and rate | About 1 s per call; 5,000 requests/h per token | Milliseconds |
| Infrastructure | None | Deployment, auth, backups, and an availability target |
| Failure mode | GitHub outage: agents cannot claim and must wait | Cluster outage: same, and the cluster is less available than GitHub today |

Phase 2 (when needed): an in-cluster read model that projects board claims into
`Lease` objects for millisecond checks by in-cluster agents, and a Re8ch MCP
tool `agent_presence_list` so the tenant tools expose the same board. GitHub
stays the authority in both.
