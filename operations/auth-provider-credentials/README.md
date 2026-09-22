# External auth provider credential workflow

This directory records the reusable process for Google and Apple login
credentials. It contains no credential values. Product or tenant directories
may link to this document and provide only their own non-secret provider names,
callback URLs, and Kubernetes Secret key mapping.

## Storage contract

Keep source credentials outside every Git checkout:

```text
~/.config/re8ch/auth-provider-credentials/
├── google-oauth-<project>.json
├── apple-sign-in-<key-id>.p8
└── apple-oauth-<service-id>.json
```

Create the directory with mode `0700` and each file with mode `0600`. Never
paste a client secret, Apple private key, signed JWT, OAuth authorization code,
or Kubernetes Secret payload into GitHub, chat, shell arguments, or command
output. Clear the clipboard and delete temporary delivery files after an
injection succeeds.

The metadata JSON files use this shape:

```json
{
  "provider": "google-or-apple",
  "client_id": "provider-client-identifier",
  "client_secret": "secret-or-signed-jwt",
  "callback_url": "https://service.example.com/auth/callback/provider",
  "expires_at": "optional ISO-8601 timestamp",
  "key_id": "optional Apple key identifier",
  "team_id": "optional Apple team identifier",
  "private_key_path": "optional absolute path to the Apple .p8 file"
}
```

`client_secret`, the Apple `.p8` file, and a signed Apple JWT are secrets.
Client IDs, Team IDs, Key IDs, and callback URLs are identifiers, but keep them
out of logs unless troubleshooting requires them.

## Google OAuth client

1. Use one **Web application** OAuth client in the Google Cloud project.
2. Configure the exact callback URL. Google performs an exact string match.
3. Publish the consent screen only after its support email, homepage, privacy
   policy, terms, and authorized domain are valid.
4. Download or record the generated client ID and secret directly into a mode
   `0600` JSON file in the storage directory above.
5. Inject only the consumer's mapped client ID and secret fields into its
   runtime Secret.

Google client secrets do not have a scheduled expiry. Rotate immediately after
suspected exposure, when ownership changes, or during the service's regular
credential rotation window. Keep the old client usable until the replacement
has passed a canary login, then revoke it.

## Sign in with Apple

1. Create or select the primary App ID and enable Sign in with Apple.
2. Create a Services ID for web login and bind it to that primary App ID.
3. Register the exact web domain and return URL.
4. Create a Sign in with Apple key, download its `.p8` file once, and save it
   with mode `0600` in the storage directory above.
5. Generate an ES256 client-secret JWT with `generate-apple-client-secret.py`.
   Apple accepts at most 15777000 seconds, about six months, between `iat` and
   `exp`.
6. Store the Services ID as the runtime client ID and the signed JWT as the
   runtime client secret. The `.p8` key must never be mounted in the gateway.

Example generation command (identifiers shown as placeholders):

```sh
python3 operations/auth-provider-credentials/generate-apple-client-secret.py \
  --team-id TEAM_ID \
  --key-id KEY_ID \
  --client-id com.example.auth.web \
  --private-key ~/.config/re8ch/auth-provider-credentials/apple-sign-in-KEY_ID.p8 \
  --output ~/.config/re8ch/auth-provider-credentials/apple-oauth-example.json
```

Generate and deploy the replacement JWT before `expires_at`. A safe rotation
keeps the Apple private key stable, generates a new JWT, updates the Kubernetes
Secret, restarts the consumer, and verifies a canary login before discarding
the expired metadata file. Revoke the Apple key only after every consumer has
moved to a JWT signed by a replacement key.

## Kubernetes delivery

Each consumer owns a mapping from provider fields to its Kubernetes Secret.
For the unified RE8CH gateway the mapping is:

| Provider field | `auth/re8ch-auth-gateway-secrets` key |
| --- | --- |
| Google client ID | `GOOGLE_OIDC_CLIENT_ID` |
| Google client secret | `GOOGLE_OIDC_CLIENT_SECRET` |
| Apple Services ID | `APPLE_OIDC_CLIENT_ID` |
| Apple signed client-secret JWT | `APPLE_OIDC_CLIENT_SECRET` |

Patch the existing Secret instead of replacing it because it also contains
unrelated gateway credentials. Build any patch in a mode `0600` temporary file,
pass it through standard input or `--patch-file`, verify only the key names,
then delete the file and clear the clipboard. Never commit the Secret or a
decrypted SOPS document.

After delivery, restart the workload only when its scheduling constraints can
place a Pod. Verify health, start and callback for each provider, one canary
login, tenant selection, and the resulting group claims before enabling Dex or
product access.

## Reusing this process in another directory

Add a short `provider-credentials.md` in the consumer directory with:

- a link to this workflow;
- the provider registration name and exact callback URL;
- the namespace, Secret name, and key mapping;
- the workload that consumes the Secret;
- the health and canary login URLs;
- the owner and rotation deadline.

Do not copy generated credential files into the consumer directory. Reuse the
secure local storage contract and create a separate runtime Secret for each
consumer so account merging does not become credential sharing.
