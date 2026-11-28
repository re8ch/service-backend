# Admin/BYOC toolchain runtime verification

Use this procedure after source or delivery changes. The dated recovery baseline,
verified release/run references, and outstanding acceptance work live in
[Issue3002](https://github.com/re8ch/service-backend/issues/3002), under
[the recovery parent](https://github.com/re8ch/service-backend/issues/2984).
Historical observations expire; read current runtime state before changing it.

## Authority and scope

- Discover the authenticated organization's current published capabilities. Re8ch
  Admin and the application organization are separate security boundaries.
- Treat both self and Atlantic as administrator-owned BYOC connections. Select the
  exact Ready connection and verify its resolved target on every result; names or
  a successful self-cluster operation do not establish remote access.
- Product source belongs in its own repository. Desired-state changes use signed
  PRs, successful checks, and the repository's existing bounded GitHub Actions SSA
  release contract. Keep the main anchor clean.
- Remote PublicEdge/AF candidates are service contracts, not local Nodes. Do not
  use remote names as local node selectors, restore authority-health scheduling,
  reuse an invalid release bundle, or invent a public route. Resolve network
  authority through [authenticated topology](../../contracts/network-topology/README.md).

## Deployment and image verification

1. Record the actual build revision separately from a metadata-only finalize
   revision. Verify immutable digest, signature, workflow identity, available
   provenance and SBOM; do not claim provenance that a legacy build did not emit.
2. Build once per source/configuration. Reuse a valid artifact for delivery and
   transport retries. An artifact rejected for invalid rendered configuration
   requires a corrected release, not reuse of the failed bundle.
3. Inspect each registry destination independently. GHCR primary success does not
   establish Harbor replication success. Record replica index, required blobs,
   digest, signature claims and cold-client verification before declaring it ready.
4. Read the node's effective registry hosts configuration and actual egress path.
   A configured application proxy does not imply containerd uses it. Do not stop
   production Harbor or erase shared content to simulate a failure. Scope canaries
   explicitly and distinguish cached objects from newly downloaded objects.
5. Use the existing release cohort. Resolve ownership conflicts narrowly with the
   required preview and UID/resourceVersion/old-value guards; avoid blanket force.
6. After the workflow, verify observed generation, updated/Ready/available replicas,
   the new Pod image digest, restart count and startup events. Old healthy replicas
   during a rolling update do not prove the new release has started.

## End-to-end acceptance

Run the same matrix on both Ready BYOC connections with the same real Admin
session, after all dependent rollouts. Preserve the first failure before retrying.

| Capability | Required evidence |
| --- | --- |
| Context/read | Correct active organization, target binding and real API read |
| Write | Preview then create an isolated, non-secret acceptance object |
| SSA | Server dry-run, apply v1 then v2 with a dedicated field manager; same UID and intended field update |
| Cleanup | Delete only the acceptance object; verify absence |
| AF service observation | Local owner and imported service/endpoint health, freshness/expiry and actual usable route |
| Automatic convergence | Controlled IP, endpoint/service and DNS changes; observe withdrawal/replacement and successful real calls within the declared convergence window |
| User path | Actual login/token flow and real Admin tool calls; Connected Accounts or tools/list alone is insufficient |

Use published operation schemas and plan hashes. Do not obtain kubeconfig, Secret
values or alternate credentials to bypass a missing operation. Do not change OIDC
as a speculative network fix. Use bounded non-secret fixtures, preserve evidence,
and avoid interrupting the user's existing authenticated session.

## Telemetry acceptance

Verify the chain independently: Core → Gateway → Cloud → Among Hub/Broker, plus
tenant-control. Private metrics exposure is intentional; do not publish it through
the public Gateway for debugging.

- Check the component endpoint, scraper `up`, and ingested build/release labels for
  each new Pod. HTTP RED measurements must identify the actual tested path.
- A generated Sentry event ID proves SDK capture only. Read the event from Sentry
  and confirm environment, actual source release, phase, operation and trace.
- Verify GitHub integration repository authorization, release commit/PR association,
  native source mappings, and the actual source/dashboard link. Provisioned files
  and deployment readiness do not establish working UI links.
- Keep operation/trace IDs in logs/events, not high-cardinality metric labels. Do
  not record tokens, request arguments, results, credentials or personal payloads.
- Preserve frontend Connection failed/UNAVAILABLE without operation IDs separately
  from backend timeouts. Report client elapsed time separately from server time.
  A successful retry does not erase an intermittent failure or a long-call cutoff.

## Diagnosing misleading health

Proxy selection labels can be correct while a Service targets the wrong listening
process. Verify EndpointSlice → target port → owning process → authenticated peer,
then download a complete immutable object and validate byte count and SHA. ICMP,
1 MiB samples or a Ready controller cannot replace that check.

A withdrawn unhealthy DNS backend can restore service-level DNS while that backend
remains broken. Record both facts. AMD recovery is deferred until core functional
acceptance, per the user's current priority; do not describe it as repaired.

## Evidence and recovery

Store bounded outcomes and run/PR links in the tracking Issue. Keep raw telemetry,
restricted inventory and secrets in approved external systems. Update the Issue
acceptance ledger as each row is actually verified. If a canary fails, preserve its
first error and maintain the last healthy replicas; roll back the owning desired
reference through its existing review/release path when required. Do not recreate
a retired writer or silently transfer object ownership to bypass the failure.
