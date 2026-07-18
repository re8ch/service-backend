# Neo4j knowledge graph

This deployment runs Neo4j Community on the `restricted-node-6dc3f140f9` database node. It does not
use the PostgreSQL wire protocol. PostgreSQL-backed business data must be read
and written through the public PostgREST boundaries:

- `https://db-ro.re8ch.com/<service>` for reads
- `https://db-rw.re8ch.com/<service>` for writes

The bootstrap job discovers the public OpenAPI surfaces for CompoCV, Image, and
Ledger and records each PostgREST service and RPC as graph nodes. Protected
business rows require an RLS-scoped PostgREST JWT supplied at query or sync-job
runtime; no JWT or PostgreSQL credential is committed here.

Installed plugins:

- APOC Core
- APOC Extended
- Graph Data Science Community Edition
- GenAI

Neo4j 2026.06's container plugin catalog does not currently publish `n10s`.
Neosemantics is therefore intentionally not enabled rather than mixing an
unverified older JAR into the database process.

The ClusterIP service remains available internally. The StatefulSet also binds
TCP `7474` and `7687` on the restricted-node-6dc3f140f9 host for the MikroTik public DNAT path:

```text
192.0.2.79:7474 -> 192.0.2.97:7474
192.0.2.79:7687 -> 192.0.2.97:7687
```

`neo4j.re8ch.com` must be DNS-only in Cloudflare for raw Bolt on TCP 7687,
unless Cloudflare Spectrum is used. A local port-forward remains available:

The browser UI is TLS-terminated by the restricted-node-6dc3f140f9 Traefik instance and is available
on the standard HTTPS endpoint:

```text
https://neo4j.re8ch.com/browser/
```

```sh
kubectl -n neo4j port-forward service/neo4j 7474:7474 7687:7687
```

Fetch the generated administrator password without writing it to disk:

```sh
kubectl -n neo4j get secret neo4j-auth \
  -o jsonpath='{.data.NEO4J_AUTH}' | base64 -d
```

Useful verification queries:

```cypher
RETURN apoc.version();
RETURN gds.version();
MATCH (s:PostgRESTSource)-[:EXPOSES]->(e:PostgRESTEndpoint)
RETURN s.name, count(e) AS endpoints ORDER BY endpoints DESC;
```

For protected row ingestion, pass a short-lived, product-scoped PostgREST JWT
as an `Authorization: Bearer ...` header to `apoc.load.jsonParams`. Keep reads
on the RO base and mutations on the RW base.
