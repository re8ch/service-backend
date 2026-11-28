# Public evidence privacy

Public repositories expose policy, code, schemas and verifiable claims. They do
not expose the private witness used to produce those claims.

The public envelope contains only a claim category, a rotating opaque subject,
the public source revision, day-level timing, coarse result bands and a
randomized commitment. A GitHub OIDC identity signs the envelope as a DSSE or
in-toto attestation.

The private witness contains identity mappings, topology, raw telemetry, exact
event timing, logs and the random commitment nonces. Store it in an approved
restricted evidence system. A private Project draft may hold bounded metadata
and an opaque locator; it is not a telemetry store.

## Privacy boundary

- Generate subject handles with `HMAC-SHA256(epoch_key, scope || real_id)` and
  rotate the key at least every 31 days. Never publish the key or input mapping.
- Commit to canonical witness records with at least 32 random nonce bytes before
  building the Merkle tree. A plain hash of an email, IP, tenant or node name is
  reversible by dictionary search and is forbidden.
- Suppress cohorts smaller than five. Publish ranges, never exact counts.
- Use day-level or coarser time buckets and delay operational health publication
  by at least 24 hours. Longer delay is appropriate for exploitable incidents.
- Keep tenant, resource, namespace, host, port, trace, operation, event and
  receipt identifiers out of public Issues, Discussions, Wiki, Pages, Actions
  summaries and artifacts.
- Treat already published data as disclosed. Deletion does not restore secrecy;
  rotate credentials or rebind identifiers when confidentiality still matters.

`policy.yaml` is the shared contract. `public-evidence.schema.json` defines the
only public payload. Validate candidate payloads with:

```sh
python operations/public-evidence/validate.py evidence.yaml
```

Runtime control and incident response always use the private witness, never a
delayed, bucketed or privacy-noised public projection.
