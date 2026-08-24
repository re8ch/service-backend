# DataSpace MVP

The public MVP endpoint is `https://storage.re8ch.com:8443`. Its Ingress is a
declaration consumed by the in-cluster edge/DNS controllers and targets the
restricted-node-6dc3f140f9 edge; do not patch public DNS manually. Public 8443 is forwarded to the
restricted-node-6dc3f140f9 Traefik `websecure` entrypoint because public 80/443 are not assumed.
Authentication uses Dex authorization code with PKCE and audience
`storage-collaborator`; a random device token remains an emergency bootstrap
path only.

The MVP metadata index is atomically persisted on the CephFS volume so Pod
restarts preserve IDs, ETags, cursors, and directory state. This is usable for
the first BYOC deployment but is not the final metadata architecture. The
PostgREST repository remains the production migration target once the
`dataspace` database service is provisioned; file bytes remain on CephFS.
