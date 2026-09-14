# RE8CH Secretary System

Secretary is the durable incident responsibility and evidence control plane. It does not treat a provider `200`, delivered/read receipt, or model statement as proof that a human accepted responsibility or that a service recovered.

The runtime is tenant-scoped in `re8ch-secretary`. Its single-node Restate deployment keeps the stable node identity `restate-865f85bb68-j7bdt` so the retained data volume continues the existing durable journals after the namespace migration.

## Runtime

- Restate `IncidentWorkflow`, `RecipientObject`, `ChannelObject`, and `InvestigatorService` run from the same image.
- FastAPI accepts signed fact events and channel callbacks.
- PostgreSQL is the authoritative append-only ledger and outbox.
- Neo4j is a rebuildable evidence graph projection.
- BlueBubbles and the restricted-node-6dc3f140f9 Voice Gateway are degraded notification adapters, never fact authorities by themselves.

Release tag `v0.2.1` resolves to immutable digest `sha256:84386d2d4cdbf5e56e48b111d05f8d7ab5367597d5250f38c7153f36d20a2969`; GitOps deploys that digest. Its build carries provenance and SBOM attestations. The managed database bundle supplies `secretary-db-credentials/DATABASE_URL_RW`. Create the `secretary-runtime` Secret out of band with keys `webhook-secret`, `query-token`, `bluebubbles-webhook-token`, `voice-webhook-token`, `identity-bindings-json`, `neo4j-uri`, `neo4j-user`, `neo4j-password`, and `litellm-api-key`; no secret values belong in Git. The out-of-band `secretary-email` Secret supplies the Stalwart application credentials, pinned TLS fingerprint, recipient address, and stable recipient ID. `identity-bindings-json` maps channel identities such as `imessage:+8613800138000` to stable recipient IDs.

Configure BlueBubbles to send `new-message`, `updated-message`, and `message-send-error` webhooks to:

```text
https://tools.re8ch.com/v1/channels/bluebubbles/<bluebubbles-webhook-token>
```

An iMessage action has the form `ACK inc_x CODE`, `HELP inc_x CODE`, or `VERIFY inc_x CODE`. `CODE` is generated per assignment; only its SHA-256 hash is stored in the `responsibility.assigned` event. Both the sender binding and code must match before the reply becomes an authenticated action.

Configure the restricted-node-6dc3f140f9 Voice Gateway with:

```text
VOICE_EVENT_WEBHOOK=https://tools.re8ch.com/v1/channels/voice/<voice-webhook-token>
```

FreeSWITCH event handling must call the authenticated Voice Gateway endpoint `POST /v1/voice/calls/{call_id}/dtmf` with digit `1`, `2`, or `3`; Secretary maps only digit `1` from a pre-bound verified destination to an ACK.

Email is the first production canary channel. The API submits through the existing Stalwart account and records separate `responsibility.assigned`, `delivery.accepted`, and `delivery.provider_accepted` facts. SMTP acceptance is not treated as device delivery, reading, acknowledgement, or task completion. The message links to a confirmation page; only the explicit form submission with its incident-bound action code creates `responsibility.acknowledged`.

Create an incident with `POST /v1/incidents` and include `notification_email` with `recipient_id`, `to`, `subject`, and `text`. Query the append-only stream with `GET /v1/event-stream?after_seq=<n>&limit=<n>` and the `X-Secretary-Query-Token` header. The stream returns a monotonic PostgreSQL sequence cursor plus the original fact event.

`secretary-cluster-watcher` observes Kubernetes Node Ready conditions and configured public HTTPS probes. Each failure transition has a deterministic incident and idempotency key, so polling does not create duplicate mail or facts.

Run the dependency-free policy tests with:

```bash
python -m unittest discover -s platform/secretary-system/app/tests -v
```
