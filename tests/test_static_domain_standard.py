import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "operations/releases/static_domain_standard.py"
spec = importlib.util.spec_from_file_location("static_domain_standard", MODULE)
standard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(standard)


def documents():
    return standard.load(standard.STANDARD), standard.load(standard.AUTHORITY), standard.load(standard.DISPATCH)


def test_standard_validates_all_nine_domains():
    definition, authority, dispatch = documents()
    standard.validate(definition, authority, dispatch)
    assert len(authority["spec"]["domains"]) == 9


def test_plan_derives_isolated_identity_and_self_hosted_runner():
    definition, authority, dispatch = documents()
    value = standard.plan("agent-runtime", definition, authority, dispatch)["spec"]
    assert value["protectedEnvironment"] == "release-executor-agent-runtime"
    assert value["audience"] == "https://kube-api.re8ch.com/release-executor-agent-runtime"
    assert value["kubernetesGroup"] == "re8ch:release-executor:agent-runtime"
    assert value["runnerLabels"] == ["self-hosted", "Linux", "X64", "r640", "untrusted-build"]
    assert value["canary"] == "toolhive-operator"
    assert value["scale"]["helmReleases"] == 1
    assert "takeover-and-idempotent-replay" in value["gates"]


def test_controller_retirement_is_last_and_zero_consumer_gated():
    definition, authority, dispatch = documents()
    value = standard.plan("networking", definition, authority, dispatch)["spec"]
    assert value["phases"][-1]["id"] == "controller-retirement"
    assert {"real-consumer-count-zero", "source-consumer-count-zero", "zero-old-controller-writes"} <= set(
        value["controllerDeletionConditions"]
    )


def test_cross_domain_environment_is_rejected():
    definition, authority, dispatch = documents()
    authority["spec"]["domains"]["identity-access"]["execution"]["protectedEnvironment"] = (
        "release-executor-agent-runtime"
    )
    with pytest.raises(ValueError, match="protectedEnvironment"):
        standard.validate(definition, authority, dispatch)
