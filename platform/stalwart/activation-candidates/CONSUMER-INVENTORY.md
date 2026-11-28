# Sanitized OIDC consumer compatibility evidence

Read-only inspection during the Stalwart self-service task. No credential, token,
user claim, client identifier or redirect URI is reproduced here. Exact non-secret
metadata and platform audit references remain in the task conversation. This is
an observed subset, not proof of complete consumer coverage or successful login.

| Consumer | Expected issuer category | Desired/live match | Redirect contract match | Scope / flow evidence |
| --- | --- | --- | --- | --- |
| DataSpace gateway | Stalwart public issuer | true | Not applicable to token verifier | Enforces configured issuer/audience; actual login acceptance pending |
| Storage Collaborator mobile client | Stalwart public issuer | Runtime installation unverified | true, source request versus declared registration | Source requests identity plus offline access, authorization code with S256 |
| DataSpace archive pilot | Stalwart public issuer | Runtime use unverified | true, source request versus declared registration | Source requests identity plus offline access, authorization code with S256 and state |
| Headlamp | GoTrue public issuer | true for selectively inspected client, issuer, callback, scopes and PKCE fields | true for source versus runtime callback; provider registration not inspected | Profile/email/offline scopes and PKCE configured; not a direct Stalwart consumer |
| Webmail | GoTrue public issuer at runtime | false: legacy source has another broker/client | Unverified | Runtime OAuth issuer differs from JMAP origin; do not confuse JMAP with OIDC |
| Main ConnecTool cloud/admin/operate entries | Legacy broker issuer | true for inspected issuer fields | Unverified | Source scopes include identity/profile/email/groups/offline; effective requests unverified |
| Main ConnecTool tenant entry | GoTrue public issuer | true for inspected issuer field | Unverified | Source request policy differs from legacy broker entries |
| GoTrue custom Stalwart upstream | Stalwart public issuer per reviewed SQL | Unverified | Unverified | Selective read denied by database permissions; client/scopes/flow not inferred |
| Historical Kubernetes pilot and other bootstrap registrations | Stalwart registration declarations only | Unverified | Declared redirect allowlists only | No evidence these registrations remain active consumers |

Reviewed `platform/stalwart/unified-identity.yaml` contains seven historical client
registrations; `idp-pilot.yaml` contains six. Those declarations include redirect
allowlists but omit explicit scopes, grantTypes and firstParty. They are not
sufficient evidence of current v1 client policy. A directory-scoping migration
rule must not be applied indiscriminately to all provider client registrations.

DataSpace is the strongest identified direct-consumer compatibility canary:
verify its issuer/audience contract and user-authorized read after migration.
Do not run the archive upload pilot as a login test: it writes user files.
The GoTrue upstream is an unresolved dependency because several current consumers
use GoTrue and could indirectly depend on that upstream login method.

## Evidence and coverage limits

- Git desired state: Storage Collaborator OIDCLogin.swift, DataSpace archive pilot,
  DataSpace gateway declarations/authenticator, Headlamp deployment, ConnecTool
  release, Stalwart registration plans, and Supabase provider-domain SQL.
- Runtime: only selected non-secret deployment environment/argument fields and
  MCPOIDCConfig issuer fields, through the authenticated platform catalog.
- GoTrue: bounded SQL selected only provider metadata; the server returned
  permission denied. No permission expansion, alternative credentials or full
  Secret read was attempted. Database metadata visibility was also empty and must
  not be interpreted as provider absence.
- Not covered: live provider OAuthClient registry, all browser/desktop clients,
  private product repositories, actual issued scopes, token validation, refresh,
  user consent, or successful end-to-end login. The isolated IdP pilot and primary
  provider store may have different registrations; their equivalence is unproven.
