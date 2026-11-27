# Interactive managed cluster transport

The installed kubectl/client-go deliberately loads kubeconfig AuthInfo only for
TLS API endpoints ([client-go source](https://github.com/kubernetes/client-go/blob/v0.33.1/tools/clientcmd/client_config.go#L208)). The reviewed Among broker currently listens on internal
HTTP. A tokenFile entry therefore does not send the projected ServiceAccount
identity and native requests return401 even though metadata bindings are Ready.
A real-kubectl regression captures this previous behavior using synthetic data.

For one verified managed-target operation, Cloud now opens a request-local
loopback relay. Kubectl receives only that loopback URL and an empty user entry.
The relay reads this Pod's projected ServiceAccount token afresh per request and
sets Authorization only toward the fixed, UID/scope-verified broker/grant URL.
The existing broker performs TokenReview and exact grant-use SAR, keeps remote
credentials private and injects its encrypted remote credential. No grant, OIDC,
API endpoint, persistent controller, global kubeconfig or permission changes.

The relay reuses the existing broker request and streaming helpers. It refuses
absolute/traversal URLs, follows no redirects and returns no Location header,
retains Content-Encoding and auth/status metadata, flushes small watch events,
and closes upstream when its caller disconnects. It never retries business
requests. Its socket and credential-free config are removed at operation end.
Current regression evidence covers real kubectl GET/SSA, token rotation,
gzip, watch/cancellation and fail-closed credentials. Upgrade protocols require
separate end-to-end validation and are not implied by HTTP/SSA acceptance.

Original caller execution proofs continue through the existing scoped Actions
path. This interactive path uses only Cloud's already approved projected
identity and does not give Actions access to the interactive Admin grant.
