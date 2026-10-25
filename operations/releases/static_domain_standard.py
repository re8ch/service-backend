#!/usr/bin/env python3
"""Validate and materialize a parameterized static GitOps domain plan."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
STANDARD = ROOT / "operations/releases/static-domain-standard.yaml"
AUTHORITY = ROOT / "operations/releases/static-domain-authority.yaml"
DISPATCH = ROOT / "operations/migrations/github-first-control-plane/static-release-domain-dispatch.yaml"
TARGET = ROOT / "operations/releases/static-control-plane.yaml"


def load(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected YAML object: {path}")
    return value


def expected(domain: str) -> dict[str, str]:
    return {
        "protectedEnvironment": f"release-executor-{domain}",
        "audience": f"https://kube-api.re8ch.com/release-executor-{domain}",
        "kubernetesGroup": f"re8ch:release-executor:{domain}",
    }


def validate(standard: dict, authority: dict, dispatch: dict | None = None) -> None:
    spec = standard["spec"]
    authority_spec = authority["spec"]
    domains = authority_spec["domains"]
    order = authority_spec["rolloutPolicy"]["order"]
    if len(domains) != 9 or set(order) != set(domains):
        raise ValueError("standard requires exactly the declared nine-domain rollout")
    if dispatch is not None:
        dispatched = {
            item["authorityDomain"]: item for item in dispatch["dispatches"]
            if item["scope"] in {"existing-autonomous-domain", "split-main-domain"}
        }
        if set(dispatched) != set(domains):
            raise ValueError("dispatch matrix and authority must cover the same nine domains")
    phases = [item["id"] for item in spec["phases"]]
    if len(phases) != len(set(phases)) or phases[-1] != "controller-retirement":
        raise ValueError("phases must be unique and controller retirement must be last")
    retirement = next(item for item in spec["phases"] if item["id"] == "controller-retirement")
    required_retirement = {
        "real-consumer-count-zero", "source-consumer-count-zero",
        "zero-old-controller-writes", "finalizers-orphaned-safely",
    }
    if not required_retirement <= set(retirement["requires"]):
        raise ValueError("controller retirement is missing zero-consumer safety gates")
    seen = {key: set() for key in expected("domain")}
    for domain, item in domains.items():
        execution = item["execution"]
        for key, wanted in expected(domain).items():
            if execution.get(key) != wanted:
                raise ValueError(f"{domain}: {key} must be {wanted}")
            if execution[key] in seen[key]:
                raise ValueError(f"{domain}: duplicate {key}")
            seen[key].add(execution[key])
        if dispatch is not None:
            declared_environment = dispatched[domain].get("protectedEnvironment")
            if declared_environment != execution["protectedEnvironment"]:
                raise ValueError(f"{domain}: dispatch protectedEnvironment differs from authority")
        profile = spec["riskProfiles"].get(item["risk"])
        if not profile or item["stabilityHours"] != profile["stabilityHours"]:
            raise ValueError(f"{domain}: stabilityHours does not match risk profile")
        if execution["workflow"] not in {
            ".github/workflows/static-release.yml", ".github/workflows/main-domain-release.yml"
        }:
            raise ValueError(f"{domain}: unsupported workflow")
    labels = spec["runner"]["labels"]
    if labels[:2] != ["self-hosted", "Linux"] or spec["runner"]["githubHostedAllowed"]:
        raise ValueError("only the declared self-hosted Linux runner profile is allowed")


def plan(domain: str, standard: dict, authority: dict, dispatch: dict | None = None) -> dict:
    dispatch = dispatch or load(DISPATCH)
    validate(standard, authority, dispatch)
    authority_spec = authority["spec"]
    if domain not in authority_spec["domains"]:
        raise ValueError(f"unknown domain: {domain}")
    item = authority_spec["domains"][domain]
    source = item["source"]
    execution = item["execution"]
    dispatched = next(
        value for value in dispatch["dispatches"]
        if value.get("authorityDomain") == domain
        and value["scope"] in {"existing-autonomous-domain", "split-main-domain"}
    )
    repository = source.get("repository") or execution["repository"]
    deletion_conditions = next(
        phase["requires"] for phase in standard["spec"]["phases"]
        if phase["id"] == "controller-retirement"
    )
    return {
        "apiVersion": "delivery.re8ch.com/static-domain-plan/v1alpha1",
        "kind": "StaticDomainPlan",
        "metadata": {"name": domain},
        "spec": {
            "risk": item["risk"],
            "stabilityHours": item["stabilityHours"],
            "scope": dispatched["scope"],
            "controllerNamespace": dispatched["controllerNamespace"],
            "repository": repository,
            "ref": source["ref"],
            "workflow": execution["workflow"],
            "protectedEnvironment": execution["protectedEnvironment"],
            "audience": execution["audience"],
            "kubernetesGroup": execution["kubernetesGroup"],
            "runnerLabels": standard["spec"]["runner"]["labels"],
            "canary": dispatched.get("recommendedCanary"),
            "dependencies": dispatched.get("dependencies", []),
            "scale": dispatched.get("staticCohort") or dispatched.get("liveSnapshot"),
            "gates": dispatched["gates"],
            "phases": standard["spec"]["phases"],
            "controllerDeletionConditions": list(deletion_conditions),
            "completion": dispatched["completion"],
        },
    }


def target_plan(domain: str, standard: dict, authority: dict, dispatch: dict) -> dict:
    current = plan(domain, standard, authority, dispatch)["spec"]
    target = load(TARGET)["spec"]
    return {
        "apiVersion": "delivery.re8ch.com/static-control-plane-plan/v1alpha1",
        "kind": "StaticControlPlanePlan",
        "metadata": {"name": domain},
        "spec": {
            "status": target["status"],
            "executionAuthorized": False,
            "domain": domain,
            "risk": current["risk"],
            "currentExecution": {key: current[key] for key in (
                "repository", "ref", "workflow", "protectedEnvironment", "audience",
                "kubernetesGroup", "runnerLabels",
            )},
            "target": target,
            "migrationGates": current["gates"],
            "stabilityHours": current["stabilityHours"],
            "controllerDeletionConditions": current["controllerDeletionConditions"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--domain", required=True)
    plan_parser.add_argument("--output", type=Path)
    target_parser = subparsers.add_parser("target", help="derive the unactivated GitHub-native target")
    target_parser.add_argument("--domain", required=True)
    target_parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    standard, authority, dispatch = load(STANDARD), load(AUTHORITY), load(DISPATCH)
    if args.command == "validate":
        validate(standard, authority, dispatch)
        print("validated static GitOps standard for 9 domains")
        return 0
    make_plan = target_plan if args.command == "target" else plan
    rendered = yaml.safe_dump(make_plan(args.domain, standard, authority, dispatch), sort_keys=False)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
