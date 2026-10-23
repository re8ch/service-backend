#!/usr/bin/env python3
"""Compile the three locked Ceph charts into reviewed static release inputs.

The GitHub workflow may fetch only the exact archive URL in this lock, verifies
the archive bytes before Helm sees them, rewrites every declared image to an
immutable multi-architecture digest, and then crosses the common bundle
boundary.  It never reads a Helm repository index at release time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import yaml

from unified_bundle import CLUSTER_SCOPED, canonical, digest, object_identity

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "contracts/releases/ceph/chart-locks.yaml"
CONTRACTS = ROOT / "contracts/releases/ceph"
RENDERED = ROOT / "operations/releases/ceph-static"
STATIC_RELEASES = ROOT / "clusters/re8ch/components/release-compat/ceph-static-releases.yaml"
CEPH_RBAC = ROOT / "clusters/re8ch/components/release-compat/ceph-executor-rbac.yaml"
WORKFLOW = ".github/workflows/ceph-static-release.yml"
SIGNER = "https://github.com/re8ch/service-backend/.github/workflows/ceph-shadow-compile.yml@refs/heads/main"

STAGE_KINDS = [
    ("crd-schema", {"CustomResourceDefinition"}, False),
    ("cluster-rbac", {"ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding"}, True),
    ("runtime", {"ServiceAccount", "ConfigMap", "Deployment", "DaemonSet", "Service"}, True),
    ("custom-resources", {"Driver", "OperatorConfig", "CephCluster", "CephBlockPool", "CephFilesystem", "CephFilesystemSubVolumeGroup"}, False),
    ("storage-class", {"StorageClass"}, False),
]
CLUSTER_KINDS = {"CustomResourceDefinition", "ClusterRole", "ClusterRoleBinding", "StorageClass", "CSIDriver"}
PROTECTED = ["CustomResourceDefinition", "StorageClass", "CephCluster", "CephFilesystem", "CephBlockPool", "CephFilesystemSubVolumeGroup"]
PLURALS = {
    "CustomResourceDefinition": "customresourcedefinitions", "ClusterRole": "clusterroles",
    "ClusterRoleBinding": "clusterrolebindings", "StorageClass": "storageclasses",
    "ServiceAccount": "serviceaccounts", "Role": "roles", "RoleBinding": "rolebindings",
    "ConfigMap": "configmaps", "Deployment": "deployments", "DaemonSet": "daemonsets", "Service": "services",
    "Driver": "drivers", "OperatorConfig": "operatorconfigs", "CephCluster": "cephclusters",
    "CephBlockPool": "cephblockpools", "CephFilesystem": "cephfilesystems",
    "CephFilesystemSubVolumeGroup": "cephfilesystemsubvolumegroups",
}


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def rewrite_images(value: object, locks: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "image" and isinstance(child, str):
                if child not in locks:
                    raise ValueError(f"unlocked image: {child}")
                value[key] = locks[child]
            else:
                rewrite_images(child, locks)
    elif isinstance(value, list):
        for child in value:
            rewrite_images(child, locks)


def download(entry: dict, directory: Path) -> Path:
    destination = directory / f'{entry["name"]}-{entry["version"]}.tgz'
    with urllib.request.urlopen(entry["url"], timeout=60) as response:  # exact archive URL, never an index
        destination.write_bytes(response.read(32 * 1024 * 1024 + 1))
    if destination.stat().st_size > 32 * 1024 * 1024 or sha256(destination) != entry["sha256"]:
        raise ValueError(f'chart digest mismatch for {entry["name"]}')
    return destination


def render(entry: dict, chart: Path) -> list[dict]:
    command = ["helm", "template", entry["releaseName"], str(chart), "--namespace", entry["namespace"]]
    for value in entry["values"]:
        path = ROOT / value["path"]
        if sha256(path) != value["sha256"]:
            raise ValueError(f'values digest mismatch: {value["path"]}')
        command += ["--values", str(path)]
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    documents = [item for item in yaml.safe_load_all(completed.stdout) if isinstance(item, dict)]
    for item in documents:
        if item["kind"] not in CLUSTER_SCOPED:
            item.setdefault("metadata", {}).setdefault("namespace", entry["namespace"])
        annotations = item.get("metadata", {}).get("annotations", {})
        if "helm.sh/hook" in annotations:
            raise ValueError(f'Helm hooks are forbidden: {object_identity(item)}')
        rewrite_images(item, entry["images"])
    documents.sort(key=object_identity)
    if len(documents) != entry["objectCount"]:
        raise ValueError(f'{entry["name"]} object count changed: {len(documents)}')
    return documents


def api_group(version: str) -> str:
    return version.split("/", 1)[0] if "/" in version else "core"


def make_contract(entry: dict, documents: list[dict]) -> dict:
    inventory = [object_identity(item) for item in documents]
    stages = []
    assigned: set[str] = set()
    for name, kinds, automatic in STAGE_KINDS:
        present = sorted({item["kind"] for item in documents if item["kind"] in kinds})
        if present:
            stages.append({"name": name, "kinds": present, "automaticRollback": automatic})
            assigned.update(object_identity(item) for item in documents if item["kind"] in kinds)
    missing = set(inventory) - assigned
    if missing:
        raise ValueError(f'unstaged Ceph objects: {sorted(missing)}')
    stages.append({"name": "health-acceptance", "kinds": [], "automaticRollback": False})
    force = [identity for identity in inventory if identity.split("/")[1] in {"Deployment", "ServiceAccount", "Role", "RoleBinding", "ConfigMap", "ClusterRole", "ClusterRoleBinding"}]
    audience = "https://kube-api.re8ch.com/release-executor-ceph-infra"
    return {
        "apiVersion": "delivery.re8ch.com/v1alpha1", "kind": "ReleaseContract", "metadata": {"name": entry["name"]},
        "spec": {
            "target": {"cluster": "re8ch", "namespace": entry["namespace"]},
            "source": {"renderer": "helm", "revision": entry["sha256"], "repository": "re8ch/service-backend",
                       "chart": entry["chart"], "version": entry["version"], "releaseName": entry["releaseName"],
                       "chartURL": entry["url"], "chartDigest": entry["sha256"],
                       "values": [{"path": value["path"], "digest": value["sha256"]} for value in entry["values"]]},
            "provenance": {"repository": "re8ch/service-backend", "workflow": WORKFLOW, "ref": "refs/heads/main",
                           "environment": "release-executor-ceph-infra", "audience": audience, "signer": SIGNER,
                           "issuer": "https://token.actions.githubusercontent.com"},
            "authorization": {"fieldManager": f're8ch-release/{entry["name"]}',
                              "resources": sorted({f'{api_group(item["apiVersion"])}/{item["kind"]}' for item in documents}),
                              "objectNames": sorted({item["metadata"]["name"] for item in documents}),
                              "allowClusterScoped": any(item["kind"] in CLUSTER_KINDS for item in documents),
                              "forceConflictObjects": force},
            "inventory": {"digest": digest(canonical(inventory))},
            "lifecycle": {"prune": True, "healthTimeoutSeconds": 1200, "protectedKinds": PROTECTED,
                          "deletionAllowlist": [identity for identity in inventory if identity.split("/")[1] not in PROTECTED],
                          "dependencies": entry.get("dependsOn", []), "stages": stages,
                          "manualRollbackKinds": PROTECTED},
        },
    }


def rbac_rule(items: list[dict], create: bool = False) -> list[dict]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for item in items:
        key = (api_group(item["apiVersion"]), PLURALS[item["kind"]])
        grouped.setdefault(key, set()).add(item["metadata"]["name"])
    result = []
    for (group, resource), names in sorted(grouped.items()):
        verbs = ["create"] if create else ["get", "patch", "update"]
        if not create and not any(item["kind"] in PROTECTED for item in items if PLURALS[item["kind"]] == resource):
            verbs.append("delete")
        if not create and resource in {"roles", "clusterroles"}:
            verbs.extend(["bind", "escalate"])
        rule = {"apiGroups": ["" if group == "core" else group], "resources": [resource], "verbs": verbs}
        if not create:
            rule["resourceNames"] = sorted(names)
        result.append(rule)
    return result


def make_rbac(all_documents: list[dict], release_names: list[str]) -> list[dict]:
    infra_kinds = CLUSTER_KINDS | {"Role", "RoleBinding"}
    infra = [item for item in all_documents if item["kind"] in infra_kinds]
    runtime = [item for item in all_documents if item["kind"] not in infra_kinds]
    # Multiple charts deliberately share some RBAC identities. Collapse them;
    # an exact object name remains the authorization boundary.
    dedupe = lambda items: list({object_identity(item): item for item in items}.values())
    infra, runtime = dedupe(infra), dedupe(runtime)
    state_names = ["ssa-state-" + name for name in release_names]
    runtime_rules = rbac_rule(runtime) + rbac_rule(runtime, create=True) + [
        {"apiGroups": [""], "resources": ["configmaps"], "resourceNames": state_names, "verbs": ["get", "patch", "update"]},
        {"apiGroups": ["apps"], "resources": ["deployments", "daemonsets"], "verbs": ["get", "list", "watch"]},
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]},
    ]
    infra_rules = rbac_rule(infra) + rbac_rule(infra, create=True) + [
        {"apiGroups": [""], "resources": ["configmaps"], "resourceNames": state_names, "verbs": ["get", "patch", "update"]},
    ]
    docs: list[dict] = []
    for name in state_names:
        docs.append({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": "rook-ceph",
                     "labels": {"delivery.re8ch.com/state": "accepted", "delivery.re8ch.com/cohort": "ceph"}}})
    for profile, rules in (("ceph-infra", infra_rules), ("ceph-runtime", runtime_rules)):
        role_name = f"release-executor-{profile}"
        binding_kind = "ClusterRoleBinding" if profile == "ceph-infra" else "RoleBinding"
        binding_meta = {"name": role_name}
        if profile == "ceph-runtime":
            binding_meta["namespace"] = "rook-ceph"
        docs += [
            {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": role_name}, "rules": rules + [
                {"apiGroups": ["delivery.re8ch.com"], "resources": ["staticreleases", "releaseoperations"], "verbs": ["get", "list"]},
                {"apiGroups": ["delivery.re8ch.com"], "resources": ["staticreleases/status", "releaseoperations/status"], "verbs": ["get", "patch", "update"]},
            ]},
            {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": binding_kind, "metadata": binding_meta,
             "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": role_name},
             "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": f"re8ch:release-executor:{profile}"}]},
        ]
    docs += [
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "release-executor-ceph-runtime-control", "namespace": "flux-system"},
         "rules": [{"apiGroups": ["delivery.re8ch.com"], "resources": ["staticreleases", "releaseoperations"], "verbs": ["get", "list"]},
                   {"apiGroups": ["delivery.re8ch.com"], "resources": ["staticreleases/status", "releaseoperations/status"], "verbs": ["get", "patch", "update"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "release-executor-ceph-runtime-control", "namespace": "flux-system"},
         "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "release-executor-ceph-runtime-control"},
         "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "re8ch:release-executor:ceph-runtime"}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "release-executor-ceph-runtime-health", "namespace": "rook-ceph"},
         "rules": [{"apiGroups": [""], "resources": ["pods/exec"], "verbs": ["create"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "release-executor-ceph-runtime-health", "namespace": "rook-ceph"},
         "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "release-executor-ceph-runtime-health"},
         "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "re8ch:release-executor:ceph-runtime"}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "release-executor-ceph-runtime-smoke", "namespace": "ceph-storage-smoke"},
         "rules": [{"apiGroups": [""], "resources": ["persistentvolumeclaims"], "resourceNames": ["rbd-smoke", "cephfs-smoke"], "verbs": ["get"]},
                   {"apiGroups": ["apps"], "resources": ["deployments"], "resourceNames": ["rbd-smoke", "cephfs-smoke"], "verbs": ["get"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "release-executor-ceph-runtime-smoke", "namespace": "ceph-storage-smoke"},
         "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "release-executor-ceph-runtime-smoke"},
         "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "re8ch:release-executor:ceph-runtime"}]},
    ]
    allowed = dedupe(infra + runtime)
    clauses = []
    by_resource: dict[tuple[str, str], set[str]] = {}
    for item in allowed:
        by_resource.setdefault((api_group(item["apiVersion"]), PLURALS[item["kind"]]), set()).add(item["metadata"]["name"])
    for (group, resource), names in sorted(by_resource.items()):
        quoted = ",".join(json.dumps(name) for name in sorted(names))
        clauses.append(f'(request.resource.group == {json.dumps("" if group == "core" else group)} && request.resource.resource == {json.dumps(resource)} && object.metadata.name in [{quoted}])')
    docs += [
        {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingAdmissionPolicy", "metadata": {"name": "ceph-executor-create-inventory"},
         "spec": {"failurePolicy": "Fail", "matchConstraints": {"resourceRules": [{"apiGroups": ["*"], "apiVersions": ["*"], "operations": ["CREATE"], "resources": ["*"]}]},
                  "matchConditions": [{"name": "ceph-executor", "expression": "request.userInfo.groups.exists(g, g in ['re8ch:release-executor:ceph-infra', 're8ch:release-executor:ceph-runtime'])"}],
                  "validations": [{"expression": " || ".join(clauses), "message": "executor create is outside the signed Ceph inventory"},
                                  {"expression": "has(object.metadata.annotations) && 'delivery.re8ch.com/release-id' in object.metadata.annotations && object.metadata.annotations['delivery.re8ch.com/release-id'] in ['rook-ceph','ceph-csi-drivers','rook-ceph-cluster'] && 'delivery.re8ch.com/operation-id' in object.metadata.annotations", "message": "executor create requires release and operation audit annotations"}]}},
        {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingAdmissionPolicyBinding", "metadata": {"name": "ceph-executor-create-inventory"},
         "spec": {"policyName": "ceph-executor-create-inventory", "validationActions": ["Deny"]}},
    ]
    return docs


def outputs() -> dict[Path, str]:
    lock = yaml.safe_load(LOCK.read_text())
    generated: dict[Path, str] = {}
    releases = []
    all_documents = []
    with tempfile.TemporaryDirectory(prefix="ceph-static-") as temporary:
        directory = Path(temporary)
        for entry in lock["charts"]:
            documents = render(entry, download(entry, directory))
            all_documents.extend(documents)
            contract = make_contract(entry, documents)
            generated[CONTRACTS / f'{entry["name"]}.yaml'] = yaml.safe_dump(contract, sort_keys=False)
            releases.append({
                "apiVersion": "delivery.re8ch.com/v1alpha1", "kind": "StaticRelease",
                "metadata": {"name": entry["name"], "namespace": "flux-system", "labels": {"delivery.re8ch.com/cohort": "ceph"}},
                "spec": {"releaseID": entry["name"], "renderer": "helm", "targetCluster": "re8ch",
                         "targetNamespace": entry["namespace"], "contractDigest": digest(canonical(contract)),
                         "chartDigest": entry["sha256"], "valuesDigest": digest(canonical(entry["values"])),
                         "bundleRepository": f'oci://ghcr.io/re8ch/service-backend-release/{entry["name"]}',
                         "dependencies": entry.get("dependsOn", []),
                         "stages": [stage["name"] for stage in contract["spec"]["lifecycle"]["stages"]]},
            })
            if entry.get("bundleDigest"):
                releases[-1]["spec"]["bundleDigest"] = entry["bundleDigest"]
    generated[STATIC_RELEASES] = "# Generated by operations/releases/compile_ceph_static.py; do not edit.\n" + yaml.safe_dump_all(releases, sort_keys=False, explicit_start=True)
    generated[CEPH_RBAC] = "# Generated by operations/releases/compile_ceph_static.py; do not edit.\n" + yaml.safe_dump_all(make_rbac(all_documents, [entry["name"] for entry in lock["charts"]]), sort_keys=False, explicit_start=True)
    return generated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render")
    parser.add_argument("--rendered-output", type=Path)
    args = parser.parse_args()
    if args.render:
        if not args.rendered_output:
            parser.error("--rendered-output is required with --render")
        lock = yaml.safe_load(LOCK.read_text())
        entry = next((item for item in lock["charts"] if item["name"] == args.render), None)
        if not entry:
            parser.error("unknown locked release")
        with tempfile.TemporaryDirectory(prefix="ceph-static-render-") as temporary:
            documents = render(entry, download(entry, Path(temporary)))
        args.rendered_output.write_text(yaml.safe_dump_all(documents, sort_keys=False, explicit_start=True))
        return 0
    generated = outputs()
    if args.check:
        stale = [str(path.relative_to(ROOT)) for path, body in generated.items() if not path.is_file() or path.read_text() != body]
        if stale:
            raise SystemExit("stale Ceph static outputs:\n" + "\n".join(stale))
        return 0
    for path, body in generated.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    print(json.dumps({"releases": 3, "outputs": len(generated)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
