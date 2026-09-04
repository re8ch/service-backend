# MCP conformance collections

Collections are authenticated, protocol-level truth tests. The runner validates protected-resource metadata, OIDC discovery and PKCE support, JWT resource audience, MCP initialization, the complete `tools/list`, every page of `resources/list`, every advertised `resources/read`, and configured domain calls.

Tokens are read only from the named broker environment reference. Evidence contains counts, stable references, response/schema hashes, operation IDs when explicitly captured, image digest and Git revision; it never contains tokens or complete business responses. Production and merge-gate runs require `MCP_EVIDENCE_COSIGN_KEY`, producing a Cosign bundle next to the manifest.

```sh
python operations/mcp-conformance/mcp_collection.py \
  operations/mcp-conformance/collections/tenant-registry-canary.json \
  --output artifacts/mcp-evidence/tenant-registry-canary.json
```
