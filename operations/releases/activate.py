#!/usr/bin/env python3
"""Verify private bootstrap artifacts and emit a reviewable ownership patch.

Never applies cluster resources. Only ciphertext is included in the patch.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import yaml

import domain_publish as publisher

ROOT = publisher.ROOT


def file_patch(path, content):
    target = ROOT / path
    if target.exists():
        before = target.read_text()
        if before == content:
            return ""
        return "*** Update File: " + str(target) + "\n@@\n" + "".join("-" + line + "\n" for line in before.splitlines()) + "".join("+" + line + "\n" for line in content.splitlines())
    return "*** Add File: " + str(target) + "\n" + "".join("+" + line + "\n" for line in content.splitlines())


def activation_files(directory):
    active = yaml.safe_load((directory / "active.yaml").read_text())
    entries = active["modules"]
    if entries != publisher.initial_modules():
        raise ValueError("bootstrap module inventory does not match current desired roots")
    publisher.bootstrap_ownership(entries)
    descriptors = {}
    approved = {}
    for entry in entries:
        module = entry["module"]
        filename = module.replace("/", "-") + ".yaml"
        descriptor = yaml.safe_load((directory / "releases" / filename).read_text())
        bundle = descriptor["bundle"]
        expected_url = "oci://" + publisher.REGISTRY + "/" + publisher.PROJECT + "/" + module.replace("/", "-")
        if descriptor["module"] != module or bundle["url"] != expected_url or bundle["identity"] != publisher.IDENTITY or bundle["issuer"] != publisher.ISSUER:
            raise ValueError("unreviewed bootstrap registry or publisher")
        approved[module] = {"identity": publisher.IDENTITY, "issuer": publisher.ISSUER}
        descriptors[module] = descriptor
    if len(list((directory / "releases").glob("*.yaml"))) != len(entries):
        raise ValueError("unexpected extra release descriptors")
    encrypted_secret = directory / "gitops-oci-reader.sops.yaml"
    # Use the worker's existing registry credential helper. Never request or
    # decrypt the cluster's age private key for onboarding.
    secret = yaml.safe_load(encrypted_secret.read_text())
    if secret.get("kind") != "Secret" or secret.get("metadata") != {"name": "gitops-oci-reader", "namespace": "flux-system"}:
        raise ValueError("unexpected encrypted pull-secret identity")
    if secret.get("sops", {}).get("age", [{}])[0].get("recipient") != "age1c7camhc8ql85t5u4hrjmsmkurfa0n0v7lg05espasa9fqapjz5vqql8lfj":
        raise ValueError("unreviewed cluster age recipient")
    if not secret.get("stringData", {}).get(".dockerconfigjson", "").startswith("ENC["):
        raise ValueError("pull credential must remain encrypted")
    with tempfile.TemporaryDirectory(prefix="re8ch-oci-verification-") as temporary:
        env = dict(os.environ)
        def verify_entry(entry):
            module = entry["module"]
            bundle = descriptors[module]["bundle"]
            reference = bundle["url"].removeprefix("oci://") + "@" + bundle["digest"]
            subprocess.run(["cosign", "verify", "--certificate-identity", publisher.IDENTITY,
                "--certificate-oidc-issuer", publisher.ISSUER, reference], env=env, check=True, stdout=subprocess.DEVNULL)
            extracted = Path(temporary) / module.replace("/", "-")
            extracted.mkdir()
            subprocess.run(["flux", "pull", "artifact", "oci://" + reference, "--output", str(extracted)], env=env,
                           check=True, stdout=subprocess.DEVNULL)
            actual = {publisher.identity(doc): doc for doc in yaml.safe_load_all((extracted / "resources.yaml").read_text())}
            expected = {publisher.identity(doc): doc for doc in publisher.bundle_documents(module, bootstrap=True)}
            if actual != expected:
                raise ValueError("published bundle differs from effective desired state: " + module)
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(verify_entry, entries))
    generator = publisher.load_module("flux_oci", "operations/releases/flux_oci.py")
    generator.approved_publishers = lambda: approved
    files = {
        "operations/releases/active.yaml": (directory / "active.yaml").read_text(),
        "operations/releases/codenames.yaml": (directory / "codenames.yaml").read_text(),
        "operations/releases/publishers.yaml": yaml.safe_dump({"version": 1, "modules": approved}, sort_keys=False),
        "clusters/re8ch/flux-system/gitops-oci-reader.sops.yaml": encrypted_secret.read_text(),
    }
    children = list(yaml.safe_load_all((ROOT / "platform/observability-source/source.yaml").read_text()))
    source_filenames = []
    for entry in entries:
        module = entry["module"]
        filename = module.replace("/", "-") + ".yaml"
        descriptor = descriptors[module]
        source, reconciliation = generator.manifests(descriptor)
        source["spec"]["secretRef"] = {"name": "gitops-oci-reader"}
        if entry.get("name"):
            child = next(doc for doc in children if doc["metadata"]["name"] == entry["name"])
            child["spec"]["sourceRef"] = reconciliation["spec"]["sourceRef"]
            child["spec"]["path"] = "./"
            child["spec"]["decryption"] = {"provider": "sops", "secretRef": {"name": "sops-age"}}
            documents = [source] # existing child name/inventory/suspension preserved
        else:
            reconciliation["spec"]["wait"] = entry["wait"]
            reconciliation["spec"]["decryption"] = {"provider": "sops", "secretRef": {"name": "sops-age"}}
            documents = [source, reconciliation]
        files["clusters/re8ch/domain-sources/" + filename] = yaml.safe_dump_all(documents, sort_keys=False)
        files["releases/" + filename] = yaml.safe_dump(descriptor, sort_keys=False)
        source_filenames.append(filename)
    files["clusters/re8ch/domain-sources/kustomization.yaml"] = yaml.safe_dump({
        "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": source_filenames}, sort_keys=False)
    files["platform/observability-source/source.yaml"] = yaml.safe_dump_all(children, sort_keys=False)
    root_path = ROOT / "clusters/re8ch/kustomization.yaml"
    root = yaml.safe_load(root_path.read_text())
    direct = {entry["module"] for entry in entries if not entry.get("name")}
    root["resources"] = [ref for ref in root["resources"] if str((root_path.parent / ref).resolve().relative_to(ROOT)) not in direct]
    root["resources"].extend(["flux-system/gitops-oci-reader.sops.yaml", "domain-sources"])
    files["clusters/re8ch/kustomization.yaml"] = yaml.safe_dump(root, sort_keys=False)
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--run", required=True, help="successful trusted bootstrap GitHub Actions run")
    args = parser.parse_args()
    run = json.loads(subprocess.check_output(["gh", "run", "view", args.run, "--repo", "re8ch/service-backend",
        "--json", "conclusion,headSha,event,workflowName"]))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if run["conclusion"] != "success" or run["headSha"] != head or run["event"] != "workflow_dispatch" or run["workflowName"] != "domain-oci-release":
        raise ValueError("bootstrap artifact must come from the successful reviewed main revision")
    files = activation_files(args.directory)
    print("*** Begin Patch\n" + "".join(file_patch(path, content) for path, content in files.items()) + "*** End Patch")


if __name__ == "__main__":
    main()
