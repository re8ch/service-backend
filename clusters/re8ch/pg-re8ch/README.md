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
