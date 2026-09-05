# MCP conformance collections

Collections are authenticated, protocol-level truth tests. The runner validates protected-resource metadata, OIDC discovery and PKCE support, JWT resource audience, MCP initialization, the complete `tools/list`, every page of `resources/list`, every advertised `resources/read`, and configured domain calls.

Dex authorization-code tokens use the dedicated OAuth client ID as `aud`; Dex
does not implement RFC 8707 resource indicators. Collections therefore declare
both the RFC 9728 `resource` URL and its one-to-one `tokenAudience` client ID and
validate both sides of that mapping. Sharing a token audience across MCP
resources is forbidden.

Tokens are read only from the named broker environment reference. Evidence contains counts, stable references, response/schema hashes, operation IDs when explicitly captured, image digest and Git revision; it never contains tokens or complete business responses. Production and merge-gate runs require `MCP_EVIDENCE_COSIGN_KEY`, producing a Cosign bundle next to the manifest.

```sh
python operations/mcp-conformance/mcp_collection.py \
  operations/mcp-conformance/collections/tenant-registry-canary.json \
  --output artifacts/mcp-evidence/tenant-registry-canary.json
```
