# Studio Toolkit release

Product source lives in the private `baozou-ppt-studio/baozou-ppt-studio` repository.
This directory publishes prebuilt API and Linux Blender worker images for the
existing RE8CH cluster. It does not activate a workload or modify public routing.

The production environment contains `STUDIO_RELEASE_DEPLOY_KEY`, a dedicated
read-only deploy key for that source repository. No user credential is copied
into CI. Harbor credentials follow the existing production release workflows.

Before dispatch, merge the product PR only after its Creative toolchain and
Studio toolkit checks pass. Dispatch `Studio Toolkit Harbor release` on main
with the full merged source SHA. The workflow checks the source belongs to main,
verifies its SSH signature against `allowed-signers`, reruns source contracts,
and runs the actual Linux Blender HTTP-to-video pipeline in the published worker
image. Failed verification stops signature publication and runtime activation.

Images remain private and are addressed by digest. BuildKit emits provenance and
SBOM attestations; Cosign signs and verifies each digest; Harbor scan reports are
retained in the release artifact, including per-package findings from Harbor’s
[vulnerability addition API](https://github.com/goharbor/harbor/blob/main/api/v2.0/swagger.yaml).
A missing detailed report fails publication; a successful scan is not a clean scan.
Review affected packages, available fixes and exposure before activation.
The `studio-toolkit` Harbor project has a 5 GiB storage quota, increased from
512 MiB through the Harbor quota API on 2026-10-05 with administrator approval.
The change was read back from quota ID 35 as 5,368,709,120 bytes; existing images
were retained. Release run `37226737325`, attempt 2, reuses the successful hosted
build after attempt 1 exhausted the previous quota. Before subsequent releases,
check project storage usage and available capacity; `prepare` currently verifies
project privacy but does not automatically increase or preflight the quota.
The optional initial-reader step creates a pull-only Harbor robot and emits two
SOPS-encrypted Kubernetes Secrets, one for the runtime and one for the database
migration namespace. Only encrypted secrets and the robot reference are uploaded.
Revoke an unused robot by its reference if activation is abandoned.

Activation is a separate reviewed GitOps change, using the release digests and
encrypted credentials. Record the release-specific assessment using
[the runtime exposure review](SECURITY-REVIEW.md), including residual findings.
Intended runtime:

- `api.re8ch.com/studio`: stateless API behind the existing HTTPS listener.
- `db-ro.re8ch.com/studio` and `db-rw.re8ch.com/studio`: product PostgREST with
  RLS roles; runtime APIs never connect using PostgreSQL wire protocol.
- Shared `cephfs-r640` holds bounded source media and result archives. This
  intentionally shares the restricted-node-6dc3f140f9 failure domain and is not cross-node HA; it
  follows the cluster default policy without a replicated-storage exception.
- An outbound-only worker pinned to restricted-node-6dc3f140f9 renders with CPU Cycles and FFmpeg.
- Operator migration jobs provision the dedicated database using the established
  `supabase-pg-re8ch` bootstrap pattern. Runtime roles cannot administer databases.

No claim of a deployed endpoint or cross-region acceptance is made by publishing
images. Acceptance must upload a storyboard through the public HTTPS API, observe
an restricted-node-6dc3f140f9 lease, download and verify the rendered video/audio and editable outputs,
and check tenant isolation, cancellation and offline recovery.

## Hosted build and Harbor-near transfer

Hosted runners build and test Linux/amd64 images, preserve OCI indexes (including
SBOM/provenance manifests), and upload checksummed private workflow artifacts.
The Macmini publisher checks every archive checksum and uses Skopeo with
`--all --preserve-digests`; a digest mismatch stops signing. It does not need a
Docker daemon. Copy semantics follow the [upstream Skopeo documentation](https://github.com/podman-container-tools/skopeo/blob/main/docs/skopeo-copy.1.md).

The existing Macmini runner needs `gh`, Python 3.11+, Skopeo, SOPS and Cosign, plus
its existing batch SSH alias `restricted-node-6dc3f140f9`. Direct GitHub TLS on this host has failed;
the job opens its own temporary loopback SOCKS tunnel and closes it on exit.
Harbor API, image transfer and signing traffic use that scoped restricted-node-6dc3f140f9 tunnel;
Macmini direct Harbor TLS failed while the same URL through restricted-node-6dc3f140f9 was healthy.
No host route, DNS setting or runner-global configuration changes. All publisher commands run in an isolated RUNNER_TEMP
directory and temporary registry authorization is removed on exit.

Because action-download setup uses the host's broken direct GitHub path, the
publisher has no `uses:` steps. It retrieves only the helper files at its exact
workflow commit through its scoped transport. Eight download workers dynamically
claim bounded 8 MiB HTTPS ranges from the same-run build artifact, preferring
direct transfer with scoped proxy fallback. Range headers, byte counts, allowed
ZIP entries and archive checksums are checked before OCI verification. Small
ranges prevent a slow connection from owning a large fraction of the archive
until its request timeout. The publisher allows up to 180 minutes for constrained
cross-region transfers. Final
scan/signature/digest and encrypted reader evidence is uploaded to a **draft**
private GitHub release `studio-toolkit-<source SHA>`. The `studio-oci` workflow
artifact retains the portable source build for three days. A draft release does
not activate a deployment. Inspect both API and worker scans before GitOps rollout.

Publisher Skopeo is pinned to v1.24.1, built without CGO using upstream's
`containers_image_openpgp` build tag, installed under
`/Volumes/A1/tools/studio-publisher/bin`. The same version is built in PR CI and
exercised with a real OCI archive containing both a Linux image and an attestation.
The test compares the copied index digest and every blob; it cannot pass by
dropping attestations or selecting only the host platform. Reproduce installation
with `CGO_ENABLED=0 go install -tags containers_image_openpgp go.podman.io/skopeo/cmd/skopeo@v1.24.1`
using Go 1.26.8 and a dedicated GOBIN.

## Runtime activation and acceptance

`render-runtime.py` accepts only the private Studio API and worker repositories
with immutable SHA-256 digests. After reviewing the release's signatures, build
metadata and Harbor scan findings, render the runtime and include it together
with the encrypted configuration and both encrypted registry readers in
`clusters/re8ch/kustomization.yaml`. The rendered database bootstrap job is an
operator migration; application requests continue through the HTTPS PostgREST
routes. The API and worker may retry while the initial migration completes.

The restricted-node-6dc3f140f9 worker advertises Blender and editable export capabilities. It does not
advertise native After Effects or PowerPoint execution on Linux. Those outputs
are editable JSX and PPTX files generated from the same scene and animation
model; desktop playback requires the corresponding licensed application.

The daily retention job uses a separate client identity with no worker-region
grants. Blob data is shared by the API replicas on CephFS, while worker staging
and render frames are ephemeral. Keep the production client credential outside
Git and use the dedicated `STUDIO_ACCEPTANCE_TOKEN` repository secret for the
product's `Studio external acceptance` workflow.

After Flux reconciles the activation commit, verify the migration job completed,
PostgREST is ready, all HTTPRoutes are accepted, and the worker claims jobs from
`cn-r640`. Dispatch the external acceptance workflow in the product repository.
It records the hosted runner's egress country, submits through the public HTTPS
API, checks an actual 90-frame video and audio timing, and preserves the archive,
preview frames and machine-readable evidence. A published image, ready Pod or
HTTP health response alone is not end-to-end acceptance.

## Initial read service placement

On 2026-10-05 both declared PostgreSQL replicas had no WAL receiver and their
last replay timestamp was 2026-09-17 02:31:02 UTC. Neither contained the Studio
role or database; the primary had both and no active replication connections.
Therefore both Studio PostgREST instances use the primary connection reference.
The separate HTTPS RO/RW routes and JWT/RLS roles remain intact. The RO instance
sets PGOPTIONS default_transaction_read_only=on and PGRST_DB_TX_END=rollback
(without client override) so requests cannot commit writes through this endpoint.
This is an explicit primary-backed read service, not replica availability.
Restore replica routing only after receiver/replay health, role/database presence,
and Studio read-after-write acceptance have been verified. No shared database
replication configuration is changed by this application deployment.
