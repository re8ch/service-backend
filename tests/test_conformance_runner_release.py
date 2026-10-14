from pathlib import Path
import yaml

ROOT=Path(__file__).parents[1]


def test_runner_pull_uses_exact_managed_secret():
    docs=list(yaml.safe_load_all((ROOT/"operations/api-conformance/runtime.yaml").read_text()))
    # Kyverno requires namespace-wide Secret reads for generate admission.
    # Do not enable cloning in the namespace containing the App private key.
    assert not any(x["kind"]=="ClusterPolicy" for x in docs)
    source=next(x for x in docs if x["kind"]=="Role" and x["metadata"]["name"]=="api-conformance-pull-source")
    assert source["rules"][0]["verbs"]==["get"]
    assert source["rules"][0]["resourceNames"]==["harbor-functions-shared-pull"]
    target=next(x for x in docs if x["kind"]=="Role" and x["metadata"]["name"]=="api-conformance-pull-target")
    assert target["rules"][1]["resourceNames"]==["harbor-functions-shared-pull"]
    assert "list" not in target["rules"][1]["verbs"]


def test_release_requires_version_scan_and_signature():
    text=(ROOT/".github/workflows/conformance-runner-release.yml").read_text()
    for required in ["merge-base --is-ancestor", "4.29.1", "HIGH,CRITICAL --exit-code 1", "cosign verify", "functions-shared/api-conformance-runner", "provenance: mode=max", "sbom: true"]:
        assert required in text
    assert "maxRegistryConcurrency = 1" in text
    assert "timeout-minutes: 30" in text


def test_sealed_runner_removes_build_tools_and_smoke_tests_runtime():
    text=(ROOT/"operations/api-conformance/Dockerfile.runner").read_text()
    assert text.count("pip uninstall --yes setuptools wheel") == 2
    assert text.count("pip uninstall --yes pip") == 2
    assert "pip check" in text and "schemathesis --version" in text
    assert "import runner, budget_hooks, requests, schemathesis" in text
    assert text.index("USER 65532:65532") < text.index("RUN RE8CH_REQUEST_BUDGET=/tmp/import-check python -c")
