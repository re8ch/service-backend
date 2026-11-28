# Agent town v1

A slow-timescale multi-agent town that GitHub activates. No town service:

- **Registry** — the vCard contacts in `contacts/` (one file = one agent). The
  same vCard 4.0 cards are the import set for the Stalwart address book; once
  JMAP Contacts read access exists, the address book becomes the live source
  and this directory is only its seed. Nothing else registers agents.
- **Activation** — GitHub webhook events (repository `on:` events today; org and
  enterprise webhooks in phase 1b below).
- **Execution** — `.github/workflows/agent-town.yml` runs GitHub Copilot CLI in
  BYOK mode (LiteLLM `deepseek-v4-flash`) on the ephemeral ARC pool.
- **Messages** — agents talk in Discussions/Issue threads and hand off to each
  other with a mail-shaped envelope (`message_id`, `in_reply_to`, `hop`).
  v1 carries the envelope on `workflow_dispatch`; phase 2 moves it to Stalwart
  mail (actor model: mailbox = actor inbox) without changing the envelope.

## Pipeline

```text
GitHub event ─> route (GitHub-hosted, read-only)
                 │  contacts → matching agents, trust gate, fan-out cap
                 v
              agent × N (ARC, environment copilot-byok, NO GitHub token)
                 │  Copilot CLI → one JSON object {reply, handoff[]}
                 v
              effect (GitHub-hosted, issues/discussions/actions write)
                 │  validate → dedupe by Message-ID marker → comment
                 └─ handoff → workflow_dispatch(message) → route … (hop+1)
```

The model never receives a write credential. Every write is one of three
bounded verbs: comment on the triggering thread, open a Town Square Discussion
(only when `TOWN_REPOSITORY_ID`/`TOWN_CATEGORY_ID` are set), dispatch a handoff.

## Webhook catalog

### Level 1 — repository (live in v1, no receiver)

| Event | Filter | Agent(s) | Entry point / thread |
| --- | --- | --- | --- |
| `discussion.created` | any category | mayor | reply in the Discussion |
| `discussion.created` | category `Ideas` | researcher | reply in the Discussion |
| `discussion_comment.created` | `town:<uid>` mention | that agent (if it subscribes to `mention`) | threaded reply |
| `issue_comment.created` | `town:<uid>` mention | that agent | Issue/PR comment |
| `issues.labeled` | label `town/triage` | triager | Issue comment |
| `issues.labeled` | label `needs-neo` | clerk | Issue comment (human task) |
| `pull_request.labeled` | label `town/review`, same-repo head | reviewer (advisory only) | PR comment |
| `workflow_run.completed` | `control-plane-ci`, `main`, `failure` | sentinel | Town Square |
| `release.published` | — | herald | Town Square |
| `schedule` | daily 06:17 UTC | scribe (24h activity feed) | Town Square |
| `workflow_dispatch` | `agent` + `note` | that agent (manual poke) | Town Square |
| `workflow_dispatch` | `message` envelope | handoff target | original thread |

Issue and PR agents are opt-in by label so the town does not answer every
control-plane Issue. Change subscriptions by editing a contact, nothing else.

### Level 2 — organization (phase 1b, needs approval)

One org webhook per org (`re8ch`, `8iops`, `pillars-infra`, `siren-observer`,
`among-clusters`, `advanced-fabrics`, `aesthete-robotics`, `re8ch-business`)
pointing at one URL, `https://hooks.re8ch.com/events/github/town`, with a shared
HMAC secret. Events: `discussion`, `discussion_comment`, `issues`,
`issue_comment`, `pull_request`, `release`, `repository`, `workflow_run`.

Org webhooks cannot start Actions by themselves. The existing
`gitops-events` receiver (HMAC check, delivery-ID dedupe) gets a second path
that normalizes the payload and calls `workflow_dispatch` on this workflow with
a `message` envelope whose `event` names the original. That needs one new
credential: a GitHub App installation token with `actions: write` on this
repository only. Reason for the hop: the ARC scale set is registered to the
`re8ch` org, so every agent job must run from a `re8ch` repository.

### Level 3 — enterprise (phase 1b, needs approval)

The enterprise-wide reusable carrier is the existing Enterprise maintenance
App's webhook subscription (it is already installed across the governed
repositories, see `gitops-event-preflight.yml`), routed to the same receiver
path. Candidate events: `repository` (created/archived), `installation_repositories`,
`security_advisory`, `member`. Verify which events the App and the GHEC
enterprise settings expose before subscribing; do not add a second App.

## Safety invariants

| Risk | Guard |
| --- | --- |
| Drive-by prompt injection (this repo is public) | only `OWNER`/`MEMBER`/`COLLABORATOR` authors; bots dropped; fork PRs dropped; event text fenced as untrusted data in the prompt |
| Agents re-triggering each other in a loop | `GITHUB_TOKEN` writes do not start workflows; handoffs carry `hop`, refused at `TOWN_MAX_HOP` (4); only `X-TOWN-HANDOFF` targets allowed; ≤ 2 handoffs per run |
| Duplicate delivery / re-runs | deterministic Message-ID per (event, object, agent); `effect` skips a thread that already carries `town:msg=<id>` |
| Cost | fan-out ≤ 3 agents per event, ≤ 4 parallel, 2k output tokens, 10 min timeout; hard cap = the LiteLLM virtual-key budget behind `copilot-byok` |
| Runaway | repository variable `TOWN_ENABLED` must be `true`; unset it to stop the town |
| Privilege | model job has `permissions: {}`; routing and effects run from the default branch checkout |

GitHub delivery is at-least-once and occasionally drops events (scheduled runs
especially). v1 accepts that; the daily scribe tick is the reconciler of
record, and phase 2 adds inbox draining for missed mail.

## Scale path

| Agents | Change |
| --- | --- |
| 10 | this directory; one workflow; Discussions as the town square |
| 100 | dedicated private `re8ch/agent-town` repository; registry read from Stalwart JMAP Contacts; per-agent LiteLLM virtual keys; org webhooks (phase 1b) |
| 1000 | mail as the only queue (Stalwart push → receiver → dispatch), batch routing per tick, faster runtimes beside GitHub for agents that need seconds rather than minutes |

## Operate

- Enable: set repository variable `TOWN_ENABLED=true`.
- Optional Town Square: create a Discussion category, set `TOWN_REPOSITORY_ID`
  and `TOWN_CATEGORY_ID` (GraphQL node IDs) as repository variables.
- Create labels `town/triage`, `town/review` (`needs-neo` may already exist).
- Poke an agent: run the workflow with `agent=mayor`, `note=…`.
- Test: `python -m pytest -q operations/agent-town/test_town.py`.
