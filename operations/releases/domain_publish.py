#!/usr/bin/env python3
"""Publish reviewed Kustomize roots as private, signed, immutable OCI bundles."""
from __future__ import annotations
import argparse
import base64
import copy
import functools
import importlib.util
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "registry.re8ch.com"
PROJECT = "gitops-controlplane"
IDENTITY = "https://github.com/re8ch/service-backend/.github/workflows/domain-oci-release.yml@refs/heads/main"
ISSUER = "https://token.actions.githubusercontent.com"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def initial_modules():
    impact = load_module("impact", "operations/ci/impact.py")
    owned = {root for config in impact.load_catalog()["domains"].values() for root in config["roots"]}
    resources = yaml.safe_load((ROOT / "clusters/re8ch/kustomization.yaml").read_text())["resources"]
    modules = []
    for resource in resources:
        path = (ROOT / "clusters/re8ch" / resource).resolve()
        if path.is_relative_to(ROOT):
            relative = str(path.relative_to(ROOT))
            if relative in owned and relative != "platform/observability-source":
                modules.append({"module": relative, "wait": False})
    for suffix, suspended in (("bootstrap", False), ("opencost-recovery", False), ("production", True)):
        entry = {"module": "platform/observability/" + suffix,
                 "name": "observability-" + suffix, "wait": True, "suspend": suspended,
                 "timeout": "15m" if suspended else "10m"}
        if suffix == "production":
            entry["dependsOn"] = [{"name": "observability-bootstrap"}]
        modules.append(entry)
    return modules


def harbor(method, path, body=None):
    credentials = (os.environ["HARBOR_USERNAME"] + ":" + os.environ["HARBOR_PASSWORD"]).encode()
    request = urllib.request.Request("https://" + REGISTRY + "/api/v2.0/" + path, method=method,
        headers={"Authorization": "Basic " + base64.b64encode(credentials).decode(), "Content-Type": "application/json"},
        data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
            return response.status, json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return 404, None
        raise RuntimeError(f"Harbor {method} {path} failed: HTTP {error.code}") from None


def identity(document):
    meta = document["metadata"]
    return (document["apiVersion"], document["kind"], meta.get("namespace", ""), meta["name"])


@functools.lru_cache(maxsize=1)
def effective_root():
    return {identity(doc): doc for doc in yaml.safe_load_all(subprocess.check_output(["kubectl", "kustomize", "clusters/re8ch"], cwd=ROOT))}


def bundle_documents(module, bootstrap=False):
    documents = list(yaml.safe_load_all(subprocess.check_output(["kubectl", "kustomize", module], cwd=ROOT)))
    retired = {identity(doc) for doc in yaml.safe_load_all((ROOT / "clusters/re8ch/retire-legacy-mcp.patch.yaml").read_text())}
    documents = [doc for doc in documents if identity(doc) not in retired]
    if bootstrap and not module.startswith("platform/observability/"):
        # Apply the root's effective desired state, including retirement patches,
        # rather than resurrecting objects filtered by the original root.
        effective = effective_root()
        documents = [effective[identity(doc)] for doc in documents if identity(doc) in effective]
    return documents


def bootstrap_ownership(modules):
    base = ROOT / "clusters/re8ch"
    config = copy.deepcopy(yaml.safe_load((base / "kustomization.yaml").read_text()))
    module_paths = {(ROOT / entry["module"]).resolve() for entry in modules}
    config["resources"] = [str((base / ref).resolve()) for ref in config["resources"]
                           if (base / ref).resolve() not in module_paths]
    config["patchesStrategicMerge"] = [str((base / ref).resolve()) for ref in config.get("patchesStrategicMerge", [])]
    for generator in config.get("configMapGenerator", []):
        files = []
        for value in generator.get("files", []):
            key, separator, ref = value.partition("=")
            files.append((key + "=" if separator else "") + str((base / (ref if separator else key)).resolve()))
        generator["files"] = files
    with tempfile.TemporaryDirectory(prefix="re8ch-ownership-") as temporary:
        config["resources"] = [os.path.relpath(ref, Path(temporary).resolve()) for ref in config["resources"]]
        (Path(temporary) / "kustomization.yaml").write_text(yaml.safe_dump(config))
        core = list(yaml.safe_load_all(subprocess.check_output(["kubectl", "kustomize", temporary,
            "--load-restrictor=LoadRestrictionsNone"], cwd=ROOT)))
    owners = {identity(doc): "core" for doc in core}
    effective = set(effective_root())
    for entry in modules:
        module = entry["module"]
        if module.startswith("platform/observability/"):
            continue # existing child inventory is independent of root inventory
        for document in bundle_documents(module, bootstrap=True):
            key = identity(document)
            if key in owners:
                raise RuntimeError(f"duplicate desired-state ownership: {key}: {owners[key]} and {module}")
            owners[key] = module
    if set(owners) != effective:
        raise RuntimeError(f"ownership split changes root inventory: missing={effective-set(owners)}, extra={set(owners)-effective}")
    return owners


def provision_reader(out):
    status, project = harbor("GET", "projects/" + PROJECT)
    if status == 404:
        harbor("POST", "projects", {"project_name": PROJECT, "metadata": {"public": "false"}})
        _, project = harbor("GET", "projects/" + PROJECT)
    if project["metadata"].get("public") != "false":
        raise RuntimeError("GitOps project must be private; refusing publication")
    robot_name = "flux-domain-reader-" + os.environ["GITHUB_RUN_ID"]
    _, robot = harbor("POST", "robots", {"name": robot_name, "description": "Flux read-only GitOps OCI source",
        "duration": -1, "level": "project", "permissions": [{"kind": "project", "namespace": PROJECT,
        "access": [{"resource": "repository", "action": "pull"}]}]})
    config = {"auths": {REGISTRY: {"auth": base64.b64encode((robot["name"] + ":" + robot["secret"]).encode()).decode()}}}
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "gitops-oci-reader", "namespace": "flux-system"},
              "type": "kubernetes.io/dockerconfigjson", "stringData": {".dockerconfigjson": json.dumps(config)}}
    # The only persisted credential artifact is encrypted to the existing Flux
    # age recipient. No decrypted Secret or robot token reaches logs/artifacts.
    encrypted = subprocess.check_output(["sops", "encrypt", "--input-type", "yaml", "--output-type", "yaml",
        "--age", "age1c7camhc8ql85t5u4hrjmsmkurfa0n0v7lg05espasa9fqapjz5vqql8lfj",
        "--encrypted-regex", "^(data|stringData)$", "/dev/stdin"], input=yaml.safe_dump(secret).encode())
    (out / "gitops-oci-reader.sops.yaml").write_bytes(encrypted)
    (out / "robot-reference.json").write_text(json.dumps({"id": robot["id"], "name": robot["name"], "project": PROJECT}))


def publish(modules, out, bootstrap=False):
    allocator = load_module("names", "operations/releases/allocate_codename.py")
    state = yaml.safe_load(allocator.STATE.read_text())
    releases = out / "releases"
    releases.mkdir(exist_ok=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for entry in modules:
        module = entry["module"]
        slug = module.replace("/", "-")
        codename = allocator.proposed(module, state)
        repository = REGISTRY + "/" + PROJECT + "/" + slug
        status, existing = harbor("GET", "projects/" + PROJECT + "/repositories/" + slug + "/artifacts/" + codename)
        if status != 404 and (existing.get("annotations") or {}).get("delivery.re8ch.com/revision") != revision:
            raise RuntimeError(f"immutable codename already exists at another revision: {module}:{codename}")
        if existing:
            digest = existing["digest"]
        else:
          with tempfile.TemporaryDirectory(prefix="re8ch-bundle-") as temporary:
            directory = Path(temporary)
            rendered = yaml.safe_dump_all(bundle_documents(module, bootstrap), sort_keys=False).encode()
            (directory / "resources.yaml").write_bytes(rendered)
            (directory / "kustomization.yaml").write_text("apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: [resources.yaml]\n")
            raw = subprocess.check_output(["flux", "push", "artifact", "oci://" + repository + ":" + codename,
                "--path", temporary, "--source", "https://github.com/re8ch/service-backend",
                "--revision", "main@sha1:" + revision, "--annotations", "delivery.re8ch.com/revision=" + revision,
                "--reproducible", "--output", "json"], cwd=ROOT)
            digest = json.loads(raw)["digest"]
        reference = repository + "@" + digest
        subprocess.run(["cosign", "sign", "--yes", reference], check=True)
        subprocess.run(["cosign", "verify", "--certificate-identity", IDENTITY,
                        "--certificate-oidc-issuer", ISSUER, reference], check=True, stdout=subprocess.DEVNULL)
        descriptor = {"apiVersion": "delivery.re8ch.com/v1alpha1", "kind": "DomainRelease", "module": module,
            "codename": codename, "bundle": {"url": "oci://" + repository, "digest": digest,
            "signature": "cosign-keyless", "identity": IDENTITY, "issuer": ISSUER}, "images": []}
        (releases / (slug + ".yaml")).write_text(yaml.safe_dump(descriptor, sort_keys=False))
        state["modules"].setdefault(module, {"releases": []})["releases"].append({"codename": codename, "digest": digest})
        print(f"Published and verified {module}:{codename}@{digest}", flush=True)
    (out / "codenames.yaml").write_text(yaml.safe_dump(state, sort_keys=False))
    (out / "active.yaml").write_text(yaml.safe_dump({"version": 1, "modules": modules}, sort_keys=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base")
    parser.add_argument("--module", help="explicitly republish one already activated module")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    status, project = harbor("GET", "projects/" + PROJECT)
    if status != 404 and project["metadata"].get("public") != "false":
        raise RuntimeError("refusing to publish private declarations to a public project")
    if args.bootstrap:
        modules = initial_modules()
        bootstrap_ownership(modules)
        provision_reader(args.output)
    else:
        config = yaml.safe_load((ROOT / "operations/releases/active.yaml").read_text())
        impact = load_module("impact", "operations/ci/impact.py")
        selected = impact.select(impact.changed_paths(args.base, "HEAD"))
        modules = [entry for entry in config["modules"] if entry["module"] in selected["roots"]]
        if args.module:
            modules = [entry for entry in config["modules"] if entry["module"] == args.module]
            if len(modules) != 1:
                raise RuntimeError("explicit module is not activated")
    publish(modules, args.output, args.bootstrap)


if __name__ == "__main__":
    main()
