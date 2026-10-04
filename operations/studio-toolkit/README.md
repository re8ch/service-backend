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
retained in the release artifact. Review vulnerability findings before activation.
The optional initial-reader step creates a pull-only Harbor robot and emits two
SOPS-encrypted Kubernetes Secrets, one for the runtime and one for the database
migration namespace. Only encrypted secrets and the robot reference are uploaded.
Revoke an unused robot by its reference if activation is abandoned.

Activation is a separate reviewed GitOps change, using the release digests and
encrypted credentials. Intended runtime:

- `api.re8ch.com/studio`: stateless API behind the existing HTTPS listener.
- `db-ro.re8ch.com/studio` and `db-rw.re8ch.com/studio`: product PostgREST with
  RLS roles; runtime APIs never connect using PostgreSQL wire protocol.
- Shared CephFS holds bounded source media and result archives.
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
Harbor traffic remains direct. No host route, DNS setting or runner-global
configuration changes. All publisher commands run in an isolated RUNNER_TEMP
directory and temporary registry authorization is removed on exit.

Because action-download setup uses the host's broken direct GitHub path, the
publisher has no `uses:` steps. It retrieves only the helper files at its exact
workflow commit and same-run build artifact through its scoped transport. Final
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
