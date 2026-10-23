#!/usr/bin/env python3
"""Validate a main-domain contract and select its bounded execution profile."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "operations/releases/static-domain-authority.yaml"
CONTRACT_ROOT = ROOT / "contracts/releases/main-domain"


def profiles() -> dict[str, dict]:
    authority = yaml.safe_load(AUTHORITY.read_text())
    return authority["spec"]["domains"]


def validate_contract(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    if CONTRACT_ROOT.resolve() not in resolved.parents or resolved.suffix not in {".yaml", ".yml"}:
        raise ValueError("contract must be a YAML file under contracts/releases/main-domain")
    contract = yaml.safe_load(resolved.read_text())
    spec = contract["spec"]
    domain = spec.get("authorityDomain")
    profile = profiles().get(domain)
    if profile is None or "bootstrap" not in profile:
        raise ValueError(f"unknown or non-bootstrap main domain: {domain!r}")
    execution = profile["execution"]
    expected = {
        "repository": execution["repository"],
        "workflow": execution["workflow"],
        "ref": "refs/heads/main",
        "environment": execution["protectedEnvironment"],
        "audience": execution["audience"],
    }
    for field, value in expected.items():
        if spec["provenance"].get(field) != value:
            raise ValueError(f"contract provenance {field} must be {value}")
    name = contract["metadata"]["name"]
    if not name.startswith(f"main-{domain}-"):
        raise ValueError("contract name does not match authorityDomain")
    return {
        "domain": domain,
        "environment": execution["protectedEnvironment"],
        "audience": execution["audience"],
        "kubernetes-group": execution["kubernetesGroup"],
        "signer": spec["provenance"]["signer"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    result = validate_contract(args.contract)
    if args.github_output:
        with args.github_output.open("a") as output:
            for key, value in result.items():
                output.write(f"{key}={value}\n")
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
