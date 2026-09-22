# RE8CH account broker connector

The `dex-service-config-reconciler` adds the `re8ch-auth` OIDC connector only
to the `https://dex.service.re8ch.com` issuer. It leaves the canonical Dex
issuer and the existing GitHub connector in place. The broker is
`https://api.re8ch.com/auth` and its registered redirect URI must be
`https://dex.service.re8ch.com/callback`.

The SOPS-managed `dex/re8ch-auth-broker-oidc` Secret has `client-id` and
`client-secret` keys. The matching client and signing key are in
`auth/re8ch-auth-broker-client`. Add `enabled: "true"` to the Dex Secret only
after the broker health check and database migration pass; verify a canary
login immediately afterward. Do not commit plaintext
credentials. Without the Dex Secret or its explicit enable
flag, the reconciler omits the connector. Enabling or rotating it updates
only `dex-config-service` and rolls
`dex-service`.

External provider credential locations and callback URLs are in
`runbooks/auth-provider-credential-intake.md`.

The broker connector requests `openid profile email groups`. Its group and
email verification exceptions are scoped to this trusted upstream. Verify a
canary login and its expected group claim before relying on group based RBAC.
