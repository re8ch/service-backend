# pg-re8ch edge gateway configuration

These files capture the live, non-Qwen PostgREST edge configuration used after
the pg-meta/pg-re8ch responsibility split.

- `haproxy/pg-re8ch-default.cfg` selects the designated restricted-node-6dc3f140f9 primary.
- `nginx/db-edge-re8ch.conf` exposes the public PostgREST service paths.
- `postgrest/*.conf.example` are secret-free templates. Replace placeholders
  from an approved root-only environment or secret store before deployment.
- `systemd/patroni-zt-rest-proxy.service` exposes only Patroni health on the
  restricted-node-6dc3f140f9 ZeroTier address for the DNS controller.

Never commit the rendered PostgREST files from `/etc/postgrest`: they contain
database passwords and JWT secrets.

`pg-re8ch-1` on restricted-node-6dc3f140f9 is the authoritative primary. `pg-re8ch-2` and
`pg-re8ch-3` are asynchronous edge read replicas. Apply
`patroni/edge-replica-tags.yaml` to both replicas: they must never be promoted,
join synchronous commit, or seed another replica. Read traffic may use them
only through a Patroni replica health check with an explicit lag threshold.
