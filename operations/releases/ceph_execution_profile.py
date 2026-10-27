"""Reviewed stage identity for the existing, unchanged Ceph signed contracts."""
import json
from pathlib import Path

from unified_bundle import canonical, digest

PROFILE = Path(__file__).with_name("ceph-runtime-profile.json")


def runtime_provenance(contract):
    profile = json.loads(PROFILE.read_text())
    if profile["contracts"].get(contract["metadata"]["name"]) != digest(canonical(contract)):
        return None
    runtime = profile["execution"]
    publisher = {**runtime, "environment": "release-executor-ceph-infra",
                 "audience": "https://kube-api.re8ch.com/release-executor-ceph-infra"}
    if any(contract["spec"]["provenance"].get(k) != v for k, v in publisher.items()):
        return None
    return runtime


def is_runtime_operation(contract, operation):
    runtime = runtime_provenance(contract)
    return runtime is not None and all(operation["github"].get(k) == v for k, v in runtime.items())
