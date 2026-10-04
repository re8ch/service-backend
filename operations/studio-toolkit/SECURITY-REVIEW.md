# Initial Studio runtime exposure review

Review date: 2026-10-05. This is a scoped deployment assessment, not a claim that
Debian media libraries are free of vulnerabilities. Keep complete reports with
each immutable image release and reassess whenever dependencies or input formats
change. The original Debian 12 release is superseded for activation.

## Evidence and release gates

Product PR 9 moves the runtime to Debian 13 with available distribution updates,
removes pip/ensurepip after dependency installation, and constrains media inputs.
The signed source `718e4bb77727081f7f5f381a0ec291249ce90570` passed
[Linux Blender/MCP rendering and scanning](https://github.com/baozou-ppt-studio/baozou-ppt-studio/actions/runs/37226488370).
Its Trivy report has 13 Critical, 289 High, 386 Medium, 385 Low and 61 Unknown
package findings, with **zero High/Critical findings having an available
Debian/Python package fix**. These are package findings, not unique CVE counts.

Media constraints passed in this same source revision. Activation additionally requires the final source's hosted native
render test, matching OCI digests, verified GitHub OIDC signatures, complete
Harbor reports, encrypted registry readers and successful GitOps CI. No scan
finding is hidden with an ignore file. A new fixable High/Critical finding fails
product CI; a missing full Harbor report fails publication.

Release [37226737325, attempt 2](https://github.com/re8ch/service-backend/actions/runs/37226737325)
completed successfully. Both OCI digests match their hosted build and verified
GitHub OIDC signatures. Harbor's complete package reports reproduce the worker
counts above; the API reports 1 Critical, 212 High, 236 Medium, 167 Low and
20 Unknown findings. Both reports contain zero fixable High/Critical findings.
The rendered runtime pins these released digests; activation still requires
successful GitOps CI and subsequent live acceptance.

## Remaining Critical package groups

| Package group | Reported issue family | Exposure controls and limits |
| --- | --- | --- |
| GDCM (3 findings) | DICOM decoding: CVE-2025-48429, CVE-2025-53618, CVE-2025-53619 | DICOM is not an accepted image format. Actual PNG/JPEG signatures and decoding are checked, and native tools receive newly encoded pixel-only PNGs. [Debian example](https://security-tracker.debian.org/tracker/CVE-2025-48429). |
| OpenEXR (2) | EXR manifest/string parsing: CVE-2026-42216, CVE-2026-42217 | EXR imports, native projects and arbitrary renderer scripts are not accepted. Output frames use PNG. [Debian advisory](https://security-tracker.debian.org/tracker/CVE-2026-42216). |
| OpenImageIO (3) | CVE-2024-55192, CVE-2024-55193, CVE-2024-55194 | Native image readers receive generated PNGs with original metadata removed. This reduces exposure to malformed imported metadata, but is not proof that every codec defect is unreachable. Debian treats these as no-DSA/minor issues in trixie; keep watching package fixes. [Debian](https://security-tracker.debian.org/tracker/CVE-2024-55192), [upstream reproducer](https://github.com/AcademySoftwareFoundation/OpenImageIO/issues/4550). |
| MariaDB client/common packages (4) | CVE-2026-44172 and CVE-2026-49261 are each reported against two packages | Studio neither starts MariaDB nor uses its client library. Runtime metadata uses HTTPS PostgREST. The wsrep shell-command issue requires a MariaDB server configuration absent from these processes. [Debian](https://security-tracker.debian.org/tracker/CVE-2026-49261). |
| libxml2 (1) | CVE-2026-6653, XML entity/subset parsing | Public input is bounded JSON and supported media, not XML/SVG or uploaded native projects. OOXML is generated from validated scene data. The package remains installed transitively. [Debian](https://security-tracker.debian.org/tracker/CVE-2026-6653). |

The audio path additionally restricts both ffprobe and ffmpeg to local-file
transport and WAV/MP3/M4A/AAC/OGG/FLAC demuxers. Real tests cover all six formats
and verify that a playlist does not contact a loopback HTTP server. Image tests
verify metadata removal, preserved RGBA pixels and rejection of a TIFF disguised
as PNG. Existing tests verify animation frames, audio trim/gain/fades and native
project creation.

## Runtime boundaries

Only granted tenant clients can upload or submit jobs. Workers have separate,
region-scoped credentials and execute generated templates, not caller code.
R640 pods run as non-root with all Linux capabilities dropped, seccomp enabled,
read-only root filesystems, bounded ephemeral storage and no service-account
token. Workers do not mount the shared API blob volume. API credentials and
short-lived PostgREST JWTs remain separate from operator database credentials.

These controls support an authenticated initial deployment with reviewed residual
package risk. They do not justify anonymous rendering, arbitrary file formats,
user-supplied scripts, native project import or distributing the worker credential.
Any expansion of those inputs requires a fresh exposure assessment. All residual
High/Medium findings remain in the full scan artifact; they are not classified as
fixed by this review. Keep this review alongside the final release digest record.

## Bounded audio update

Signed product revision `d7e812ffb3276d920ad4c5864728104e258325d4`
(product PR #10) bounds mixed audio by sample count and output bytes and checks
the resulting PCM frame count. Its [Linux rendering and scan checks](https://github.com/baozou-ppt-studio/baozou-ppt-studio/actions/runs/37231406089) passed.
Release [37231677589](https://github.com/re8ch/service-backend/actions/runs/37231677589)
produced these matching hosted-build, Harbor and verified OIDC signature digests:

- API: `sha256:464849d200f577041cf55c3d67c5ea12a04c718601b88513a2db9d944c52c03a`
- Worker: `sha256:bdeb2ea3da8be388802058840ae05cbc7bc062d1d811643991d0ce385b42c475`

Full Harbor reports retain exactly the API and worker severity counts recorded
above, with zero fixable High/Critical findings. Input formats and runtime
privileges are unchanged. Schema SQL is unchanged, so the completed bootstrap
Job retains its original image. Public acceptance must be repeated after rollout.
