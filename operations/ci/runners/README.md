# Self-hosted Actions runners

Portable Linux CI runs on the repository-scoped restricted-node-6dc3f140f9 runner with labels
`self-hosted`, `Linux`, `X64`, `restricted-node-6dc3f140f9`, and `untrusted-build`. Host-bound A1
audits remain on the Macmini runner with labels `macmini-a1` and
`trusted-audit`.

restricted-node-6dc3f140f9 runs an eight-instance repository runner pool. Every instance has its own
registration, credentials, installation directory, `_work`, and temporary
state; only the locked `github-runner` Unix account and its rootless Podman
service are shared. With 80 logical CPUs and 247 GiB RAM, eight is the reviewed
default concurrency. Do not raise `R640_RUNNER_POOL_SIZE` above 16 without a
resource and registry-egress review.

`install-r640-runner-pool.sh` idempotently expands an already configured base
runner. Generate a short-lived repository registration token outside Git, pass
it through `RUNNER_REGISTRATION_TOKEN`, and run the script as root on restricted-node-6dc3f140f9. The
token is used only by `config.sh` and is never written to repository files.
restricted-node-6dc3f140f9 must provide `python-is-python3` and `python3-pip` because repository
workflows intentionally use the portable `python -m pip` interface.

Image publishers that use `docker/setup-buildx-action` remain on the self-hosted
Macmini. Buildx's `docker-container` driver is not compatible with the restricted-node-6dc3f140f9
rootless Podman API, while ordinary `docker run`, validation, chart publishing,
and the Mihomos release jobs run on restricted-node-6dc3f140f9. No workflow uses a GitHub-hosted runner.
The control-plane CI policy runs `tests/check_no_github_hosted_runners.py` and
rejects any workflow job that does not explicitly select `self-hosted`.

## CodeQL boundary

CodeQL uses advanced setup from the checked-in
`.github/workflows/codeql-control-plane.yml` workflow. It analyzes only GitHub
Actions and Python in `.github/workflows`, `operations`, and `tests`, on restricted-node-6dc3f140f9.
Language auto-detection, default setup, application paths, compiled languages,
Swift, and GitHub-hosted runners are outside this repository's CodeQL contract.
The workflow runs only for matching changes, a single weekly schedule, or a
manual dispatch, and each analysis has a 30-minute timeout. Default CodeQL
queries are the compute budget; expanding languages, paths, queries, cadence,
runner classes, or timeout requires a reviewed policy change.

`tests/check_codeql_control_plane_scope.py` enforces that contract in the normal
control-plane policy job. The organization Code Security configuration attached
to this repository must keep default setup disabled and advanced setup allowed.
The repository variable `CODEQL_CONTROL_PLANE_ENABLED` is an activation interlock:
leave it `false` until GitHub Code Security is licensed and the enforced
`service-backend static GitOps` configuration reports an attached state. Set it
to `true` only after both conditions are verified. A skipped job does not acquire
a runner.

## restricted-node-6dc3f140f9 trust boundary

The runner service uses the dedicated, locked `github-runner` account. It has
no sudo membership, Kubernetes credentials, cluster service-account token, or
access to `/home/restricted-node-6dc3f140f9/.kube/config`. The Docker CLI and Buildx point at the
dedicated account's rootless Podman socket; the rootful Docker service is
disabled and the runner is not a member of the `docker` group. Release
credentials continue to be issued per workflow by GitHub or the credential
broker; they are never installed on the host.

restricted-node-6dc3f140f9 reaches GitHub and public registries through the host-network endpoint of
`mihomos-cluster` at `127.0.0.1:17891`. The checked-in `restricted-node-6dc3f140f9.env` contains only
non-secret proxy routing. Install it as the runner directory's `.env`, owned by
`github-runner`. Install `podman-proxy.conf` as the user-systemd override for
`podman.service`, reload the user manager, and restart both the Podman and runner
services. This lets API-driven Buildx pulls use the same egress path. Private and
cluster CIDRs stay in `NO_PROXY`; do not add an ad-hoc route or replace the
declared restricted-node-6dc3f140f9 public authority.

Install `containers.conf` under `/home/github-runner/.config/containers/`.
Registry layers are fetched serially because several concurrent Docker Hub
streams can be assigned to slow global exits by Mihomos.

Verification from restricted-node-6dc3f140f9:

```sh
curl --fail --proxy http://127.0.0.1:17891 https://api.github.com/rate_limit
sudo -u github-runner docker run --rm docker.io/library/alpine:3.22 /bin/true
sudo systemctl status actions.runner.re8ch-service-backend.r640-service-backend.service
```

The runner may execute untrusted build and validation code, so workflows using
it must not assume host trust or grant standing infrastructure credentials.
Protected release environments and short-lived broker grants remain mandatory
for material writes.
