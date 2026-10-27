# Self-hosted Actions runners

Portable Linux CI runs on the repository-scoped R640 runner with labels
`self-hosted`, `Linux`, `X64`, `r640`, and `untrusted-build`. Host-bound A1
audits remain on the Macmini runner with labels `macmini-a1` and
`trusted-audit`.

R640 runs an eight-instance repository runner pool. Every instance has its own
registration, credentials, installation directory, `_work`, and temporary
state; only the locked `github-runner` Unix account and its rootless Podman
service are shared. The eight-instance enterprise pool is also authorized for
`service-backend`, providing 16 eligible slots without changing either pool's
reviewed size. With 80 logical CPUs and 247 GiB RAM, do not raise either pool
above 16 without a resource and registry-egress review.

GitOps repositories in other enterprise organizations use an additional
enterprise-scoped pool installed by
`install-r640-enterprise-runner-pool.sh`. It reuses the same generic host,
proxy, rootless container, and workflow-installed tool contract; it does not
add repository-specific tools. Keep the repository pool until the enterprise
pool is online and cross-organization workflow acceptance succeeds.

The enterprise runner group is named `GitOps R640`. Repository access is
explicit rather than enterprise-wide: each authorized organization must also
select the GitOps repositories allowed to use the inherited group. The
machine-readable scope and the boundary between enterprise and repository
controls live in
`operations/migrations/github-first-control-plane/enterprise-github-governance.yaml`.

`install-r640-runner-pool.sh` idempotently expands an already configured base
runner. Generate a short-lived repository registration token outside Git, pass
it through `RUNNER_REGISTRATION_TOKEN`, and run the script as root on R640. The
token is used only by `config.sh` and is never written to repository files.
R640 must provide `python-is-python3` and `python3-pip` because repository
workflows intentionally use the portable `python -m pip` interface. Project
tools and Python packages are declared and installed by each workflow; they are
not part of the runner host image.

All R640 instances set `AGENT_TOOLSDIRECTORY` to the shared
`/home/github-runner/actions-toolcache`. Setup actions therefore download pinned
Go, Node, Helm, kubectl, and similar GitHub release assets once per version
instead of once per runner directory. Go module and build caches and Trivy's
database are likewise persistent under the locked runner user's home directory.
Workflows must disable remote `actions/setup-go` caching on R640; downloading a
large archive is slower than compiling against these local caches. These caches
are performance state only and are never release evidence or a credential store.
Both pool installers prewarm the shared tool cache from the existing base runner
with `rsync --ignore-existing`, avoiding a simultaneous cold download from all
new slots.

Control-plane CI, dependency review, all tenant Go release jobs, the
release-compat-controller image publisher and main-domain SSA publication/reconcile
run on GitHub-hosted Ubuntu 24.04. The tenant Go publisher creates a disposable
Buildx builder on that worker; it does not load the R640 builder index or a
Macmini Docker Desktop context. The compatibility image publisher keeps its
exact protected artifact-publisher OIDC identity and bounded Harbor copy path.
Other legacy publishers still have self-hosted declarations pending migration.
Their Docker use is not inherently host-bound: some were placed locally because
Harbor uploads from the cloud stalled, so cloud registry transfer must be tested
alongside runner changes. Private-document verification retains its protected
isolated runner; hardware and mounted-volume audits remain host-bound.
The control-plane CI policy runs `tests/check_no_github_hosted_runners.py` and
permits only those reviewed cloud jobs and rejects other undeclared hosted jobs.

## CodeQL boundary

CodeQL is disabled for this repository. Default setup, language auto-detection,
and repository-owned CodeQL workflows are prohibited because this repository is
an infrastructure desired-state control plane and includes application gitlinks
and small client implementations that must not silently expand the scan. YAML,
Kustomize, Helm, policy, dependency, image, secret, and runtime checks remain in
their dedicated self-hosted workflows. Re-enabling CodeQL requires a separate
reviewed change with an explicit cost and capacity decision.

## R640 trust boundary

The runner service uses the dedicated, locked `github-runner` account. It has
no sudo membership, Kubernetes credentials, cluster service-account token, or
access to `/home/r640/.kube/config`. The Docker CLI and Buildx point at the
dedicated account's rootless Podman socket; the rootful Docker service is
disabled and the runner is not a member of the `docker` group. Release
credentials continue to be issued per workflow by GitHub or the credential
broker; they are never installed on the host.

R640 reaches public registries through the host-network endpoint of
`mihomos-cluster` at `127.0.0.1:17891`. GitHub repository transport also stays on
that route because direct GitHub, codeload, and release-asset connectivity is
intermittent. Do not put GitHub asset domains in `NO_PROXY`; Mihomos routes the
`GITHUB` policy through a region-pinned, measured global exit so runner action
setup and pinned tool downloads cannot be randomly assigned to a low-throughput
exit. The checked-in `r640.env` contains only
non-secret routing. Install it as every runner directory's `.env`, owned by
`github-runner`. Install `podman-proxy.conf` as the user-systemd override for
`podman.service`, reload the user manager, and restart both the Podman and runner
services. Private and cluster CIDRs stay in `NO_PROXY`; do not add an ad-hoc
route or replace the declared R640 public authority.

Install `docker-config.json` as `/home/github-runner/.docker/config.json`. Its
standard Docker `proxies.default` block passes the same Mihomos route into
Dockerfile `RUN` sandboxes as proxy build arguments. Sandboxes use the declared
R640 node service identity `10.250.0.128:17891`, because their loopback is not
the host loopback. This is a generic egress contract for arbitrary builds;
workflows remain responsible for declaring all project tools and dependencies.

Image validation on R640 uses the shared named BuildKit builder
`r640-mihomos`. Its daemon runs with host networking and explicit HTTP(S) proxy
environment so `127.0.0.1:17891` resolves to the host's Mihomos listener. Every
runner `.env` sets `BUILDX_BUILDER=r640-mihomos`; without that setting, a plain
`docker build` can silently create an unproxied `default` bridge builder. Create
or repair the named builder while all R640 runner slots are idle:

```sh
sudo -u github-runner env \
  XDG_RUNTIME_DIR=/run/user/1002 \
  DOCKER_HOST=unix:///run/user/1002/podman/podman.sock \
  BUILDX_BUILDER=r640-mihomos \
  operations/ci/runners/configure-r640-buildkit.sh
```

The configuration script recreates the builder and therefore discards its
local build cache. It must not run concurrently with Actions jobs. The checked-in
`buildkitd.toml` sends Docker Hub source pulls, including pinned Alpine bases,
through the internal Harbor proxy cache; all other public registry and Dockerfile
build traffic uses the runner's Mihomos egress contract. Runtime Dockerfiles
should copy pinned tools and CA bundles from digest-pinned stages instead of
calling Alpine package repositories during every build.

Install `containers.conf` under `/home/github-runner/.config/containers/`.
Registry layers are fetched serially because several concurrent Docker Hub
streams can be assigned to slow global exits by Mihomos.

Verification from R640:

```sh
curl --fail --proxy http://127.0.0.1:17891 https://api.github.com/rate_limit
sudo -u github-runner docker run --rm docker.io/library/alpine:3.22 /bin/true
sudo -u github-runner env \
  XDG_RUNTIME_DIR=/run/user/1002 \
  DOCKER_HOST=unix:///run/user/1002/podman/podman.sock \
  BUILDX_BUILDER=r640-mihomos \
  docker buildx inspect r640-mihomos
sudo systemctl status actions.runner.re8ch-service-backend.r640-service-backend.service
```

The runner may execute untrusted build and validation code, so workflows using
it must not assume host trust or grant standing infrastructure credentials.
Protected release environments and short-lived broker grants remain mandatory
for material writes.
