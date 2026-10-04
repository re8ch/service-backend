# File-source schema provenance

Registry-server v1.5.0 pins ToolHive Core v0.0.28 in its go.mod.

- upstream-registry.schema.json: https://github.com/stacklok/toolhive-core/blob/v0.0.28/registry/types/data/upstream-registry.schema.json
- server.schema.json: https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json

These unmodified schemas were retrieved on 2026-10-04. Tests resolve the external server-schema reference from these local files and do not retrieve remote references.
