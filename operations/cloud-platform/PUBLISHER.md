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

The existing Cloud validate job now runs on a disposable GitHub-hosted runner
with the same pinned containerd daemon and Docker driver. Its single actual
runtime-image build uses this publisher against a temporary registry bound only
to `127.0.0.1:5000`. The same immutable-index gates run before the existing
packaged API/schema smoke reuses that already-built image. Only synthetic local
credentials are used; no Harbor secret is available to validation. Native
validation evidence is retained even if a gate fails, and the temporary registry
is always removed. This checks the actual Docker/BuildKit JSON formats before
production publication. The gate accepts both BuildKit SLSA v0.2 metadata and
v1 `buildDefinition`/`runDetails.metadata.buildkit_metadata` structures; unknown
or incomplete provenance is rejected.

## Independent primary and Harbor replica

The Cloud primary is now `ghcr.io/re8ch/service-backend-cloud-platform`. This
public-source-only package is linked by its OCI source label to this repository,
and is published by the existing job-scoped `GITHUB_TOKEN`/`packages: write`.
Source-repository public visibility and the specific reviewed package visibility
are checked before building; finalization checks actual visibility and repository
association again before signing. No
visibility or organization-wide permission is changed. This image contains
source plus credential-free inventory/catalog/workspace declarations, so its public-source-only classification is specific to these audited inputs. Runtime credentials
are injected later and are never Docker build arguments or copied image inputs.

The GHCR primary is signed, verified and independently cold-read before its
finalized artifact and native Sentry release metadata are published. The cold
read uses a new OCI content store and verifies every downloaded blob's SHA256,
size and descriptor graph. The test client's resolution of Harbor is redirected
to a failing loopback port; the actual production Harbor is never stopped or
changed. Raw layers and authentication files are removed, not uploaded as public
workflow artifacts. The signed immutable primary remains usable if Harbor fails.

The existing finalize job then uses native `oras cp --recursive` to copy the
whole graph and OCI referrers into the existing Harbor project, without another
build. Native `cosign triangulate` locates v2 legacy signature/attestation/SBOM tags,
which ORAS copies without rewriting their bytes. Actual CI rejected `cosign copy`
because its synthetic `sha256-DIGEST` image tag collides with the OCI referrers
fallback index; that tag must never be force-overwritten.
The destination also receives a separate truthful signature naming its own
repository; copying the source claim alone is insufficient. Both repository
claims and the common digest are checked cryptographically. Source referrer
digests must be preserved, and a separate empty content store cold-reads Harbor
and checks every blob. Only then is a Harbor image handoff emitted. A failed
replica is explicitly recorded as degraded; it does not disguise failure as full
success or block the verified GHCR primary. The existing successful-build reuse
path can retry transfer/finalization without recompiling the product.

The native PR validation extends its same single image build to two temporary
loopback registries. An ephemeral test-only signing key and an OCI referrer prove
recursive copying, destination repository identity, independent cold reads and
client-only replica outage behavior; no private production credential or public
transparency-log upload is involved. The original API/schema smoke is retained.

This producer replica is distinct from node pull routing. Existing node registry
configuration may still rewrite GHCR through Harbor. A warm node cache, a Pod
Ready state or an untested default-endpoint fallback is not proof of an
independent cold pull, especially after a cache digest mismatch. Runtime promotion
must separately demonstrate the approved opaque GHCR pull identity and a real
Harbor-independent pull path. No K3s restart or node registry change is performed
by this source PR. The existing tenant-control Harbor image is not rebuilt merely
to mirror it; its already-verified signature/source must remain the transfer
basis for any later GHCR import.

### Visibility readback correction

The first successful GHCR build (source
`c472221acf8b2a132807832df680a23a3ec94224`, run `45208776164`, index
`sha256:89241538d949fad4e6b680e26982650f3c6b8f5debf3402eb46d456c16566986`)
read back as `public`, so the initial private-visibility gate correctly stopped
finalization. No signed primary or replica was claimed from that failed finalize.
The published image is retained for successful-build reuse, not rebuilt.

For this specific system image, the source repository was independently read
back as public. Its fixed Dockerfile copies only public tracked source and
credential-free inventory/catalog/workspace/schema declarations; the Go build
stage derives from public source. Fresh hosted checkout does not initialize
catalog gitlinks. Registry authorization lives in runner temporary storage,
outside those COPY inputs, and is not a Docker build argument. Runtime Secrets,
service-account tokens, Sentry DSN and operator credentials are injected later and
are not image inputs. This is a source/COPY-closure classification, not permission
to make arbitrary application or tenant-data images public.

The reviewed expectation for this exact source-only package is therefore public,
with both the actual source-repository public flag and package-to-repository
association required before publication/finalization. The workflow does not
change package visibility. If the source becomes private or image inputs change
to include private data, publication must be re-reviewed; this exception cannot
be inferred for other images. The original successful build run can be finalized
again with its certified source SHA, SLSA, SBOM and immutable index intact.
