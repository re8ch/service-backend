# RE8CH account broker connector

The `dex-service-config-reconciler` adds the `re8ch-auth` OIDC connector only
to the `https://dex.service.re8ch.com` issuer. It leaves the canonical Dex
issuer and the existing GitHub connector in place. The broker is
`https://api.re8ch.com/auth` and its registered redirect URI must be
`https://dex.service.re8ch.com/callback`.

Provision a Secret named `re8ch-auth-broker-oidc` in the `dex` namespace with
`client-id` and `client-secret` keys. Use the same values for
`AUTH_OIDC_CLIENT_ID` and `AUTH_OIDC_CLIENT_SECRET` in the auth gateway Secret.
Do not commit either Secret. Without the Dex Secret, the reconciler omits the
connector. Adding or rotating it updates only `dex-config-service` and rolls
`dex-service`.

The broker connector requests `openid profile email groups`. Its group and
email verification exceptions are scoped to this trusted upstream. Verify a
canary login and its expected group claim before relying on group based RBAC.
