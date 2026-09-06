from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
OBJECTS = [item for item in yaml.safe_load_all((ROOT / "platform/neo4j/release.yaml").read_text()) if item]


def test_neo4j_uses_exact_official_chart():
    source = next(item for item in OBJECTS if item["kind"] == "HelmRepository")
    release = next(item for item in OBJECTS if item["kind"] == "HelmRelease")
    assert source["spec"]["url"] == "https://helm.neo4j.com/neo4j"
    assert release["spec"]["chart"]["spec"]["version"] == "2026.6.0"


def test_neo4j_is_suspended_and_reuses_primary_pvcs_only():
    release = next(item for item in OBJECTS if item["kind"] == "HelmRelease")
    assert release["spec"]["suspend"] is True
    assert release["metadata"]["annotations"]["gitops.re8ch.com/scope"] == "primary-only-no-ledger"
    values = release["spec"]["values"]
    assert values["volumes"]["data"]["volume"]["persistentVolumeClaim"]["claimName"] == "data-neo4j-0"
    assert values["additionalVolumes"] == [
        {"name": "plugins", "persistentVolumeClaim": {"claimName": "plugins-neo4j-0"}}
    ]
    assert "ledger" not in yaml.safe_dump(values).lower()
