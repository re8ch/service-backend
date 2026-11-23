# Cloud image publication

Run `45190080936` (source `f0ccebc180b3ed99ad59b26ed949e8d830502cdd`)
completed compilation, SPDX scanning and OCI index construction at 14:05:47Z,
then stalled in the BuildKit registry exporter's `pushing layers` for 1746 seconds.
Its sole bounded attempt was canceled at 14:34:52Z. No successful release artifact
or signature exists for that failed upload; neither its printed intermediate
index nor its source run qualifies for finalization reuse.

The existing Cloud publisher now uses Docker Engine's push path while retaining
BuildKit's OCI attestations. The hosted job uses the pinned official
`docker/setup-docker-action` with an isolated daemon and containerd image store,
then the native `docker` Buildx driver. It builds once with `--load`,
`--provenance=mode=max`, and the existing pinned SPDX generator, and pushes the
whole image index with `docker push` (no `--platform`). Registry credentials stay
in the existing isolated Docker authorization file and are never build arguments.
No registry, OIDC or routing authority changes.

This uses Docker's documented [attestation image-store support](https://docs.docker.com/build/metadata/attestations/#driver-and-image-store-support),
[official containerd runner configuration](https://github.com/docker/setup-docker-action#daemon-configuration),
and [index-preserving push behavior](https://docs.docker.com/reference/cli/docker/image/push/#options).
The classic store cannot preserve attestations. The opt-in
`TENANT_IMAGE_REQUIRE_ATTESTATIONS=1` mode therefore checks the actual store and
fails rather than falling back to the classic provenance-disabled path. Existing
unrelated classic publishers retain their existing behavior. The R640 recovery
choice does not reconfigure its shared daemon; it must already satisfy the same
store requirement or fail explicitly.

Before emitting an immutable `.image` reference, the publisher reads the remote
index at its immutable digest, checks its exact content hash, the linux/amd64
subject and referenced attestation manifest, then resolves native Buildx
`.Provenance` and `.SBOM` evidence from that same index. Max-mode source metadata,
exact current source revision and nonempty SPDX packages are mandatory. Missing
or mismatched evidence fails publication; it does not rebuild or retry through
a downgraded transport. The successful release artifact retains `.index.json`,
`.provenance.json`, and `.sbom.json` for independent review alongside `.image`.
The existing finalize job signs the entire index digest, covering its attestation
manifests, and publishes native Sentry release metadata for the certified source.

The CLI regression runs the actual publisher with a recording Docker executable:
one build/load plus one index push succeeds only with matching evidence; classic
stores, missing provenance/SBOM, wrong source, wrong attestation subject and
changed index bytes all fail without an image handoff. This regression validates
the publisher's behavior; it is not evidence of a real containerd upload. The
actual hosted release and remote evidence checks remain required before runtime
promotion. No local Docker daemon was available during development.
