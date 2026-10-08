# pg-re8ch single-instance authority

The owner requested a single R640 PostgreSQL instance on 2026-10-07 to avoid
WAL traffic through low-bandwidth remote hosts. `pg-re8ch-topology.json` is the
machine-readable desired topology. The live database is pg-re8ch-1 at
10.250.0.128, reached through AdvFab. There is no automatic database failover.

## Runtime reconciliation

- R640 Patroni remains enabled. Its advertised SQL/REST identities use AdvFab.
- Patroni dynamic configuration uses synchronous_mode=false and
  postgresql.use_slots=false. Do not recreate retired physical slots.
- On zan and domain-ex, verify pg_is_in_recovery() before disabling/stopping
  patroni.service. Preserve their data directories; they are retired snapshots,
  not current replicas or failover candidates.
- Disable R640 patroni-dcs-tunnel-pg-re8ch-2/3 units. R640 DCS stays local.
- After both receivers stop, remove only inactive physical slots pg_re8ch_2 and
  pg_re8ch_3. Never remove WAL files manually.
- GitOps owns pg-re8ch-primary and the empty pg-re8ch-replicas EndpointSlice.
  Read-only credentials target the primary Service and retain read-only grants.
- Keep backup jobs and archives. Reintroducing replicas requires a new approved
  topology, bandwidth planning, data catch-up/reseed and acceptance testing.

The host-mapped r640-rbd/pigsty-r640 image was expanded online to 256Gi during
recovery after its original 100Gi filesystem filled. Capacity is declared in
storage-fabric/values.yaml; this is independent of PostgreSQL data retention.
