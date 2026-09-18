# RE8CH Secretary System

Secretary is a durable interaction-evidence ledger and reasoning graph. It is
not a notification middleware and does not impose a shared message schema on
heterogeneous services. Each service and runtime keeps its native notification
mechanism; Secretary records the route from upstream event to runtime,
endpoint, group or stable recipient ID, and records only what each observer can
actually prove.

Alertmanager posts firing and resolved groups to the internal-only endpoint
`/v1/integrations/alertmanager`. This side channel records a receiver invocation
without starting an incident, sending mail, or claiming Telegram delivery.
IncidentRelay remains responsible for delivery to Telegram. Incident workflow,
email, BlueBubbles and voice endpoints remain explicit optional applications.

The runtime is tenant-scoped in `re8ch-secretary`. Its single-node Restate deployment keeps the stable node identity `restate-865f85bb68-j7bdt` so the retained data volume continues the existing durable journals after the namespace migration.

## Runtime

- Restate `IncidentWorkflow`, `RecipientObject`, `ChannelObject`, and `InvestigatorService` run from the same image.
- `secretary-channel-registry-v1` declaratively registers outbound delivery endpoints in durable Restate `ChannelObject` state. Each entry uses a stable endpoint ID and records its platform destination, purpose, severity band, owner, service, IncidentRelay channel/route linkage, and delivery path. The registry intentionally contains no bot token or route credential.
- FastAPI accepts signed observations, topology declarations, legacy incident
  facts and channel callbacks.
- PostgreSQL is the authoritative append-only ledger and outbox.
- Neo4j is a rebuildable interaction/evidence graph projection.
- BlueBubbles and the R640 Voice Gateway are degraded notification adapters, never fact authorities by themselves.

Release tag `v0.3.2` resolves to immutable linux/amd64 digest `sha256:7101b40a67eac49a6d4cc0045660dd457d42887c5f6491c788ef136adebfd3fb`; GitOps deploys that digest. Its build carries provenance and SBOM attestations. The managed database bundle supplies `secretary-db-credentials/DATABASE_URL_RW`. Create the `secretary-runtime` Secret out of band with keys `webhook-secret`, `query-token`, `bluebubbles-webhook-token`, `voice-webhook-token`, `identity-bindings-json`, `neo4j-uri`, `neo4j-user`, `neo4j-password`, and `litellm-api-key`; no secret values belong in Git. The out-of-band `secretary-email` Secret supplies the Stalwart application credentials, pinned TLS fingerprint, recipient address, and stable recipient ID. `identity-bindings-json` maps channel identities to stable recipient IDs.

Configure BlueBubbles to send `new-message`, `updated-message`, and `message-send-error` webhooks to:

```text
https://tools.re8ch.com/v1/channels/bluebubbles/<bluebubbles-webhook-token>
```

An iMessage action has the form `ACK inc_x CODE`, `HELP inc_x CODE`, or `VERIFY inc_x CODE`. `CODE` is generated per assignment; only its SHA-256 hash is stored in the `responsibility.assigned` event. Both the sender binding and code must match before the reply becomes an authenticated action.

Configure the R640 Voice Gateway with:

```text
VOICE_EVENT_WEBHOOK=https://tools.re8ch.com/v1/channels/voice/<voice-webhook-token>
```

FreeSWITCH event handling must call the authenticated Voice Gateway endpoint `POST /v1/voice/calls/{call_id}/dtmf` with digit `1`, `2`, or `3`; Secretary maps only digit `1` from a pre-bound verified destination to an ACK.

Email is the first production canary channel. The API submits through the existing Stalwart account and records separate `responsibility.assigned`, `delivery.accepted`, and `delivery.provider_accepted` facts. SMTP acceptance is not treated as device delivery, reading, acknowledgement, or task completion. The message links to a confirmation page; only the explicit form submission with its incident-bound action code creates `responsibility.acknowledged`.

Create an incident with `POST /v1/incidents` and include `notification_email` with `recipient_id`, `to`, `subject`, and `text`. Query the append-only stream with `GET /v1/event-stream?after_seq=<n>&limit=<n>` and the `X-Secretary-Query-Token` header. The stream returns a monotonic PostgreSQL sequence cursor plus the original fact event.

`secretary-cluster-watcher` observes Kubernetes Node Ready conditions and
configured public HTTPS probes. Each failure transition has a deterministic
interaction and idempotency key. It records evidence and never creates mail or
an incident by default.

## Interaction ledger

- `POST /v1/observations` appends an open-ended native event/status observation.
- `POST /v1/topology/declarations` registers an intended route separately from
  observed routes.
- `GET /v1/interactions/{id}`, `GET /v1/topology`, and
  `GET /v1/discrepancies` expose the evidence and declared/observed differences.
- A provider/runtime receipt with authoritative evidence is adoptable directly.
  Supporting evidence requires two independent failure-domain/method pairs and
  combined confidence of at least `0.90`. Logs never prove delivery.
- Original message bodies remain in the source system or an object store. The
  ledger stores hashes, schema/media references, artifact references and small
  routing metadata only. Direct email, phone and Telegram chat identities are
  rejected for recipient nodes; use stable recipient IDs and an out-of-band
  identity binding.

The API uses bounded per-operation PostgreSQL connections. `/healthz` is a
process liveness check; `/readyz` performs a bounded database probe. The runtime
may override only the host/port portion of the opaque managed DSN so credentials
remain owned by the tenant database service.

Run the dependency-free policy tests with:

```bash
python -m unittest discover -s platform/secretary-system/app/tests -v
```
