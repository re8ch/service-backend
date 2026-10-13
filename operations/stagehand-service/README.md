# RE8CH Stagehand service

Private Stagehand API for the cluster. It creates a session in the existing
Steel browser, attaches Stagehand over CDP, and sends inference to the in-cluster
LiteLLM gateway. The service is intentionally ClusterIP-only and supports one
active session because the deployed Steel runtime is single-session.

Runtime Secret `stagehand-runtime` is provisioned out of band in `ai-operations`:

- `api-token`: bearer token for this service;
- `litellm-api-key`: scoped LiteLLM virtual key.

No provider credential is stored in this workload. See the manifest comments
for endpoints. Delete a session when finished so Steel releases Chromium.
