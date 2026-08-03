# Public publication gate

This is a reviewable draft, not an installable production public plugin yet.
The configured universal MCP URL is reserved but must remain closed until every
gate below is proven.

## Required infrastructure

- Public HTTPS `https://tools.re8ch.com/mcp` with a valid certificate.
- OAuth 2.1 authorization-code flow with PKCE S256.
- RFC 9728 metadata at
  `https://tools.re8ch.com/.well-known/oauth-protected-resource`.
- Authorization-server discovery, a supported CIMD/DCR/predefined client, and
  the exact OpenAI callback URI supplied by the submission portal.
- `resource=https://tools.re8ch.com/mcp` echoed through authorization/token
  requests and enforced as the token audience by ToolHive.
- Per-user read scope only; internal operate/admin KSA surfaces are never routed
  through this hostname.
- TLS origin allowlist, rate limits, audit, Prometheus/OTLP and privacy-safe
  responses verified before Ingress creation.

## Submission materials still requiring publisher input

- Verified OpenAI developer/business identity and Apps Management write access.
- Website, support, privacy policy, terms, logo and country availability.
- Domain challenge token from the plugin submission portal.
- Reviewer credentials that work without MFA/private-network access.
- Five positive and three negative reviewer test cases.

## Current routing blocker (2026-08-04)

The cluster contains an Authentik Ingress for `auth.re8ch.com`, but the public
hostname currently reaches Pigsty nginx instead: it presents a private Pigsty
certificate and returns a Pigsty 404 for OIDC discovery. Fix public DNS/TLS
routing before creating an Authentik OAuth client or exposing the MCP Ingress.

## Draft test cases

Positive cases: platform health summary; tenant workload inventory; tenant
metrics summary; Harbor/Registry health; healthy database route comparison.

Negative cases: restart a Deployment; retrieve database/Harbor credentials;
patch or deprecate a ToolHive publication. All must remain unavailable on the
public observe surface.
