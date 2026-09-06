from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCOPE = yaml.safe_load((ROOT / "inventory/platform/gitops-convergence-scope.yaml").read_text())
RELEASES = [item for item in yaml.safe_load_all((ROOT / "platform/retained-runtimes/releases.yaml").read_text()) if item]


def test_product_exclusions_are_explicit_and_disjoint():
    spec = SCOPE["spec"]
    assert spec["excludedProducts"] == ["qwen", "qianwen-ops", "ledger"]
    managed = {item["name"] for group in ("retainAndAdopt", "retire") for item in spec[group]}
    assert not managed.intersection(spec["excludedProducts"])


def test_retained_runtime_releases_are_safe_for_owner_cutover():
    releases = {item["metadata"]["name"]: item for item in RELEASES if item["kind"] == "HelmRelease"}
    assert set(releases) == {"nuclio", "re8ch-ai-gateway"}
    assert all(item["spec"]["suspend"] is True for item in releases.values())
    assert releases["nuclio"]["spec"]["chart"]["spec"]["version"] == "0.21.26"
    assert releases["re8ch-ai-gateway"]["spec"]["chartRef"] == {"kind": "OCIRepository", "name": "litellm-helm"}


def test_litellm_chart_is_digest_pinned_and_values_are_secret_gated():
    source = next(item for item in RELEASES if item["kind"] == "OCIRepository")
    assert source["spec"]["url"] == "oci://ghcr.io/berriai/litellm-helm"
    assert source["spec"]["ref"]["digest"] == "sha256:df9be29316b0eda2b7df5ba6077a39aeb818cf20fb19b01ea4bd8937af1063b3"
    release = next(item for item in RELEASES if item.get("metadata", {}).get("name") == "re8ch-ai-gateway")
    assert release["spec"]["valuesFrom"] == [
        {"kind": "Secret", "name": "re8ch-ai-gateway-flux-values", "valuesKey": "values.yaml"}
    ]


def test_retirement_scripts_never_target_excluded_products():
    scripts = []
    for path in (
        ROOT / "platform/retire-auth-observability/retirement.yaml",
        ROOT / "platform/retire-netbird-fleet-supabase/retirement.yaml",
    ):
        if not path.exists():
            continue
        for item in yaml.safe_load_all(path.read_text()):
            if item and item.get("kind") == "Job":
                scripts.append(item["spec"]["template"]["spec"]["containers"][0]["args"][0].lower())
    combined = "\n".join(scripts)
    for excluded in SCOPE["spec"]["excludedProducts"]:
        assert excluded not in combined
