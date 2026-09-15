#!/usr/bin/env python3
"""Validate one immutable release lock and emit its module-local Flux source.

This command only emits YAML; it never applies resources or replaces ownership.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]


def approved_publishers() -> dict:
    return yaml.safe_load((ROOT / "operations/releases/publishers.yaml").read_text())["modules"]


def source_name(module: str) -> str:
    slug = "domain-" + re.sub(r"[^a-z0-9-]", "-", module.lower())
    return slug if len(slug) <= 63 else slug[:54] + "-" + hashlib.sha256(module.encode()).hexdigest()[:8]


def manifests(release: dict) -> list[dict]:
    schema = json.loads((ROOT / "schemas/domain-release.schema.json").read_text())
    jsonschema.validate(release, schema)
    catalog = yaml.safe_load((ROOT / "operations/ci/domain-catalog.yaml").read_text())
    owned = {root for config in catalog["domains"].values() for root in config["roots"]}
    if release["module"] not in owned:
        raise ValueError("release module has no reviewed Kustomize owner")
    bundle = release["bundle"]
    if bundle["signature"] != "cosign-keyless":
        raise ValueError("only reviewed GitHub OIDC keyless bundles are supported for automatic promotion")
    if not bundle["identity"].startswith("https://github.com/re8ch/"):
        raise ValueError("bundle signer must belong to the reviewed re8ch organization")
    publisher = approved_publishers().get(release["module"])
    if publisher != {"identity": bundle["identity"], "issuer": bundle["issuer"]}:
        raise ValueError("module publisher has not been independently reviewed or signer identity differs")
    if any(image["tag"] != release["codename"] for image in release["images"]):
        raise ValueError("image tags must equal the module release codename")
    name = source_name(release["module"])
    metadata = {"name": name, "namespace": "flux-system", "annotations": {
        "delivery.re8ch.com/module": release["module"],
        "delivery.re8ch.com/codename": release["codename"],
    }}
    source = {
        "apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "OCIRepository",
        "metadata": metadata,
        "spec": {
            "interval": "10m", "url": bundle["url"], "ref": {"digest": bundle["digest"]},
            "verify": {"provider": "cosign", "matchOIDCIdentity": [{
                "issuer": "^" + re.escape(bundle["issuer"]) + "$",
                "subject": "^" + re.escape(bundle["identity"]) + "$",
            }]},
        },
    }
    reconciliation = {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
        "metadata": metadata,
        "spec": {"interval": "10m", "retryInterval": "1m", "timeout": "5m",
                 "path": "./", "prune": False, "wait": True,
                 "sourceRef": {"kind": "OCIRepository", "name": name}},
    }
    return [source, reconciliation]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    args = parser.parse_args()
    release = yaml.safe_load(args.release.read_text())
    print(yaml.safe_dump_all(manifests(release), sort_keys=False), end="")


if __name__ == "__main__":
    main()
