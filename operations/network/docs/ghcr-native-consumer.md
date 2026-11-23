# Canonical GHCR consumer activation

Status: proposed; this document and unit tests do not prove runtime activation.

The existing `registry-authority/k3s-registry-authority-installer` owns this
node configuration. Its historical service-backend Flux labels are not an
authorization to restore a Flux writer. The component's reviewed source remains
`clusters/re8ch/components/registry-authority/node-installer.yaml`; no current
network-gitops release contract contains these objects.

## Registry and authentication contract

- Canonical Cloud reference: `ghcr.io/re8ch/service-backend-cloud-platform@sha256:…`.
- Independent Harbor replica:
  `registry.re8ch.com/functions-shared/re8ch-cloud-platform@sha256:…`.
  The index digest must be identical and independently verified. This is not
  the old `proxy-ghcr` rewrite path.
- Canonical GHCR must not depend on a Harbor cache being healthy. Remove its
  mirror entry from the durable registry file and the reviewed Harbor host
  from GHCR's active hosts file. An optional cache is not enabled by this change;
  do not claim native fallback guarantees for arbitrary corrupt HTTP-200 data.
- Preserve all existing opaque `configs` authentication/TLS bytes and every
  unrelated mirror. Preserve GHCR root/canonical TLS and header settings.
  Harbor host headers never migrate to the GHCR origin. Private, mode-0600
  original-file backups retain removed host configuration on the same node;
  neither credentials nor file contents belong in Git or activation logs.
- Keep existing imagePullSecrets. Cloud's current Harbor reference alone does
  not authorize the new private package: the reviewed runtime change must append
  existing `ai-operations/ghcr-re8ch`, without reading its values, replacing the
  Harbor secret or changing package visibility.

Package visibility must be classified from the actual publisher receipt. If an
approved public-source package is public, its successful canary does not prove
private-package authorization, even when imagePullSecrets are present. Retain
that gap until a separately approved private artifact is tested; never change
visibility or claim a public pull authenticated a private package.

## Native mechanism and narrow helper

Actual R640 metadata reported containerd `v2.2.3-k3s1`, with
`config_path=/var/lib/rancher/k3s/agent/etc/containerd/certs.d`. Its GHCR file had
the GHCR default server plus a Harbor `pull,resolve` host, so the default server
alone did not establish an independent canonical path.

[containerd 2.2.3 hosts configuration](https://github.com/containerd/containerd/blob/v2.2.3/docs/hosts.md)
is hot-read without daemon restart. [K3s registry configuration](https://docs.k3s.io/installation/private-registry)
is generated at K3s startup; its disabled-default-endpoint setting applies to
explicit mirror entries. Omitting the GHCR mirror keeps its normal canonical
endpoint on a later startup as well.

The concrete native gap is safe in-place transformation of these existing files
without restarting K3s or reserializing opaque credentials. The small stdlib
helper is embedded verbatim in the existing ConfigMap, not a new controller,
registry, credential synchronizer or polling service. It validates both targets
before writing, guards the exact active hosts-dir, refuses unknown authorities,
symlinks and unsupported YAML layouts, writes atomically, and supports a node-
and-file-bound check fingerprint. Existing file changes invalidate that check.

This activation only touches `/etc/rancher/k3s/registries.yaml` and the GHCR
`hosts.toml` (and their private backups). It does not rewrite `/etc/hosts`, delete
another owner's proxy drop-ins, change public authority, restart K3s/containerd,
or acquire new Kubernetes permissions. The DaemonSet's image, resources,
strategy, privileges and startup-only lifecycle remain unchanged.

## Reviewed activation and acceptance

1. Merge signed source with required CI. Use the existing Admin MCP resource
   preview/apply path for the exact ConfigMap; if legacy field ownership blocks
   it, inspect the exact manager/path and use a reviewed UID/resourceVersion/
   old-value guarded change. Do not enable broad takeover or a new writer.
2. Read the exact R640 installer Pod UID and mounted helper SHA after ConfigMap
   refresh. Do not assume a mounted update executed: the existing Pod sleeps
   after startup and this change deliberately does not trigger a fleet rollout.
3. Run `/config/install.sh --check` through that existing installer Pod. Review
   the two-file change metadata, source SHA and node-bound fingerprint. Run the
   same reviewed helper with `--expected-state <fingerprint>` once. If the
   response is unknown, read back and check again instead of blindly replaying.
  Files are atomic individually, not a cross-file transaction. An I/O failure
  reports incomplete/read-back-required and must not be called zero writes;
  a fresh check identifies the remaining work without replaying completed writes.
4. Verify durable mirror absence, unchanged opaque-config bytes, canonical-only
   active host metadata, source SHA, permissions and unchanged native process
   PID/start identity. Other registries, host mapping and proxy files must stay
   byte-identical. Continue online nodes individually; an offline node remains
   unknown and is not force-deleted.
5. Only an isolated client/hosts-dir may simulate Harbor HTTP errors or corrupt
   cache data. Never stop production Harbor, flush its cache or remove production
   containerd content to make a test cold. Test the exact native version's host
   selection and hot reload, preserving TLS verification and immutable SHA checks.
6. Use the reviewed private-package canary in `ai-operations` with the existing
   opaque `ghcr-re8ch` imagePullSecret, no service-account token, no Service and a
   pinned actual index digest. Verify manifest authorization, imageID and all
   expected bytes/SHA. `Always` plus old cached layers is not a full cold pull:
   inspect preexisting content metadata and explicitly record cached/absent bytes.

R640's observed containerd processes had no proxy environment-variable names;
Mihomo's current transparent modes were disabled. Thus native pulls are presently
direct-only. A separate memory-client `--proxy` experiment does not prove native
containerd crossed Atlantic. Record the actual native socket/egress evidence,
cold byte count and throughput; coordinate the existing network owner if a proxy
consumption contract is needed. Do not infer geography from a registry hostname
or introduce a proxy endpoint that is not an OCI registry.

Raw activation receipts, auth-preservation checks, failures and throughput belong
in private task artifacts, not Git. Full cold/private native acceptance is still
pending the signed artifact, reviewed activation and actual canary.
