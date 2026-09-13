# RE8CH Secretary System

Secretary is the durable incident responsibility and evidence control plane. It does not treat a provider `200`, delivered/read receipt, or model statement as proof that a human accepted responsibility or that a service recovered.

## Runtime

- Restate `IncidentWorkflow`, `RecipientObject`, `ChannelObject`, and `InvestigatorService` run from the same image.
- FastAPI accepts signed fact events and channel callbacks.
- PostgreSQL is the authoritative append-only ledger and outbox.
- Neo4j is a rebuildable evidence graph projection.
- BlueBubbles and the restricted-node-6dc3f140f9 Voice Gateway are degraded notification adapters, never fact authorities by themselves.

Release tag `v0.1.5` resolves to immutable digest `sha256:314f75733c5432b10253fccbdc3170b5556db8b5f34963d00b796b7982336d34`; GitOps deploys that digest. Its build carries provenance and SBOM attestations, is signed by the public key in `cosign.pub`, and passed the Harbor scan with no critical vulnerabilities. The managed database bundle supplies `secretary-db-credentials/DATABASE_URL_RW`. Create the `secretary-runtime` Secret out of band with keys `webhook-secret`, `bluebubbles-webhook-token`, `voice-webhook-token`, `identity-bindings-json`, `neo4j-uri`, `neo4j-user`, `neo4j-password`, and `litellm-api-key`; no secret values belong in Git. `identity-bindings-json` maps channel identities such as `imessage:+8613800138000` to stable recipient IDs.

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

Run the dependency-free policy tests with:

```bash
python -m unittest discover -s platform/secretary-system/app/tests -v
```
