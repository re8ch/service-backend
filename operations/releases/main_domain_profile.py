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
    if execution["repository"] != "re8ch/service-backend":
        raise ValueError(
            f"domain execution authority moved to {execution['repository']}; "
            "dispatch its protected workflow instead"
        )
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
    if str(path) != f"contracts/releases/main-domain/{name}.yaml":
        raise ValueError("use the canonical contract path so release concurrency cannot be bypassed")
    if not name.startswith(f"main-{domain}-"):
        raise ValueError("contract name does not match authorityDomain")
    return {
        "release-id": name,
        "namespace": spec["target"]["namespace"],
        "domain": domain,
        "environment": execution["protectedEnvironment"],
        "audience": execution["audience"],
        "kubernetes-group": execution["kubernetesGroup"],
        "signer": spec["provenance"]["signer"],
    }


def replay_provenance(contract: dict) -> dict[str, str] | None:
    spec = contract["spec"]
    execution = profiles().get(spec.get("authorityDomain"), {}).get("execution", {})
    replay = execution.get("replay")
    if not replay or execution.get("repository") != "re8ch/service-backend":
        return None
    original = spec["provenance"]
    expected = {"repository": execution["repository"], "workflow": execution["workflow"],
                "environment": execution["protectedEnvironment"], "audience": execution["audience"],
                "ref": "refs/heads/main"}
    if any(original.get(key) != value for key, value in expected.items()):
        return None
    return {**expected, **{key: replay[key] for key in ("workflow", "environment", "audience")}}


def replay_matrix() -> list[dict]:
    result = []
    for path in sorted(CONTRACT_ROOT.glob("*.yaml")):
        contract = yaml.safe_load(path.read_text())
        replay = replay_provenance(contract)
        if replay is None:
            continue
        relative = path.relative_to(ROOT)
        profile = validate_contract(relative)
        result.append({**profile, "contract": str(relative), "environment": replay["environment"],
                       "audience": replay["audience"]})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--contract", type=Path)
    inputs.add_argument("--replay-matrix", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.replay_matrix:
        print(json.dumps({"include": replay_matrix()}, separators=(",", ":")))
        return 0
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
