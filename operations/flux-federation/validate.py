#!/usr/bin/env python3
"""Static safety checks for the staged Flux federation bootstrap."""

from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
FEDERATION = ROOT / "clusters/re8ch/flux-federation"
NAMESPACES = {
    "flux-agent-system",
    "flux-identity-system",
    "flux-observability-system",
    "flux-network-system",
}
CONTROLLERS = {
    "source-controller",
    "kustomize-controller",
    "helm-controller",
    "notification-controller",
}


def documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    controller_docs = documents(FEDERATION / "controllers.yaml")
    deployments = [doc for doc in controller_docs if doc["kind"] == "Deployment"]
    require(len(deployments) == 16, "expected four controller Deployments per domain")
    for item in deployments:
        metadata = item["metadata"]
        require(metadata["namespace"] in NAMESPACES, f"unexpected controller namespace: {metadata}")
        require(metadata["name"] in CONTROLLERS, f"unexpected controller: {metadata}")
        args = item["spec"]["template"]["spec"]["containers"][0].get("args", [])
        require("--watch-all-namespaces=false" in args, f"controller watches outside its namespace: {metadata}")
        require(all("watch-all-namespaces=true" not in arg for arg in args), f"all-namespace watch enabled: {metadata}")

    serialized = (FEDERATION / "controllers.yaml").read_text()
    require("cluster-admin" not in serialized, "federated controller references cluster-admin")

    registrations = documents(FEDERATION / "registrations.yaml")
    sources = [doc for doc in registrations if doc["kind"] == "GitRepository"]
    reconciliations = [doc for doc in registrations if doc["kind"] == "Kustomization"]
    require(len(sources) == len(reconciliations) == 4, "expected four sources and four Kustomizations")
    for item in sources + reconciliations:
        require(item["metadata"]["namespace"] in NAMESPACES, "registration outside autonomous namespace")
    for item in sources:
        require(item["spec"].get("suspend") is False, "bootstrap source must be active for read-only verification")
    for item in reconciliations:
        require(item["spec"].get("suspend") is True, "bootstrap registration must be suspended")
        require(item["spec"].get("prune") is False, "bootstrap Kustomization must not prune")

    kustomization = yaml.safe_load((FEDERATION / "kustomization.yaml").read_text())
    resources = set(kustomization["resources"])
    require(not any(path.startswith("guardrails/") for path in resources), "bootstrap must not adopt domain guardrails")
    require(len(list((FEDERATION / "guardrails").glob("*.yaml"))) == 4, "expected one staged guardrail file per domain")

    for path in (FEDERATION / "secrets").glob("*.sops.yaml"):
        payload = path.read_text()
        require("sops:" in payload and "ENC[AES256_GCM" in payload, f"unencrypted deploy key: {path}")
        require("PRIVATE KEY-----" not in payload, f"plaintext private key marker: {path}")

    authority = yaml.safe_load((ROOT / "operations/flux-federation/authority.yaml").read_text())
    require(set(authority["spec"]["domains"]) == {"agent-runtime", "identity-access", "observability-metering", "networking"}, "authority domain mismatch")
    require(all(entry["state"] == "staged" for entry in authority["spec"]["domains"].values()), "bootstrap authority must remain staged")
    print("Flux federation bootstrap validation passed")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
