import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_module("public_site_validator", ROOT / "operations/public-sites/validate.py")
quality = load_module("public_site_quality", ROOT / "operations/public-sites/quality.py")


def catalog():
    return yaml.safe_load((ROOT / "inventory/public-sites.yaml").read_text())


def test_public_site_catalog_is_valid():
    validator.validate(catalog())


def test_primary_hostname_must_be_declared():
    changed = copy.deepcopy(catalog())
    changed["spec"]["sites"][0]["deployments"][0]["hostnames"] = ["other.example.com"]
    with pytest.raises(AssertionError, match="primary URL host"):
        validator.validate(changed)


def test_origins_are_isolated_by_deployment():
    changed = copy.deepcopy(catalog())
    global_origin = changed["spec"]["sites"][0]["deployments"][0]["origin"]
    changed["spec"]["sites"][0]["deployments"][1]["origin"] = global_origin
    with pytest.raises(AssertionError, match="isolated origin reused"):
        validator.validate(changed)


def test_static_delivery_uses_direct_or_compiled_artifacts_and_native_publishers():
    deployments = catalog()["spec"]["sites"][0]["deployments"]
    assert {item["build"]["mode"] for item in deployments} <= {"direct", "compiled"}
    assert {item["publish"]["tool"] for item in deployments} == {
        "cloudflare-wrangler", "tencent-cos-api",
    }
    assert all(item["publish"]["replace"] is True for item in deployments)


def test_publisher_must_match_the_origin_provider():
    changed = copy.deepcopy(catalog())
    changed["spec"]["sites"][0]["deployments"][1]["publish"]["tool"] = "cloudflare-wrangler"
    with pytest.raises(AssertionError, match="does not match origin"):
        validator.validate(changed)


def test_headlamp_snapshot_is_current():
    output = yaml.safe_load((ROOT / "platform/headlamp/public-site-governance.yaml").read_text())
    payload = json.loads(output["data"]["governance.json"])
    re8ch = next(site for site in payload["sites"] if site["id"] == "re8ch")
    assert {item["id"]: item["status"] for item in re8ch["deployments"]} == {
        "global": "healthy",
        "china": "healthy",
    }


def test_quality_matrix_contains_both_re8ch_deployments():
    rows = quality.matrix(catalog()["spec"])
    assert {(row["site"], row["deployment"]) for row in rows} == {
        ("re8ch", "global"),
        ("re8ch", "china"),
    }
