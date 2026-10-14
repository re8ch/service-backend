import importlib.util
import json
from pathlib import Path

import pytest

BASE = Path(__file__).parents[1] / "operations/api-conformance"


@pytest.fixture
def delivery(monkeypatch):
    monkeypatch.syspath_prepend(str(BASE))
    spec = importlib.util.spec_from_file_location("delivery_test", BASE / "delivery.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def target():
    return {"metadata": {"name": "target"}, "spec": {"repository": "org/repo", "commit": "a"*40,
        "branch": "main", "imageDigest": "sha256:"+"b"*64, "schema": {"sha256": "c"*64}, "github": {"installationId": "123"}}}


def run(m):
    t = target()
    return {"metadata": {"name": "run"}, "spec": {"targetRef": "target", "mode": "read", "commit": t["spec"]["commit"],
        "imageDigest": t["spec"]["imageDigest"], "publication": m.authority(t)}}


def test_snapshot_survives_target_change(delivery):
    t=target()
    r=run(delivery)
    t["spec"]["repository"]="other/repo"
    assert delivery.publication_target(r)["spec"]["repository"] == "org/repo"
    r["spec"]["commit"]="d"*40
    with pytest.raises(delivery.ContractError):
        delivery.publication_target(r)


def test_report_projection_and_delivery(delivery):
    r=run(delivery)
    raw=delivery.failed_report(r)
    raw["responseBody"]={"token":"private"}
    raw["counts"]["password"]="sensitive"
    clean=delivery.project_report(raw,r)
    assert "private" not in json.dumps(clean) and "password" not in json.dumps(clean)
    d=delivery.delivery_record(r,clean,{"repositoryId":"42","ownerId":"7"},"2099-01-01T00:00:00Z")
    assert d["claimed"] is False and d["report"]["commit"] == "a"*40
    assert d["report"]["summary"]==clean


@pytest.mark.parametrize("field,value",[("runId","other"),("status","passed"),("seed","secret"),
    ("failureFingerprints",["body"]),("auditRefs",["https://evil.invalid"]),("lifecycle",{"cleanup":"response body"})])
def test_invalid_reports_fail_closed(delivery,field,value):
    r=run(delivery);raw=delivery.failed_report(r);raw[field]=value
    with pytest.raises(delivery.ContractError):delivery.project_report(raw,r)


def test_unconfigured_repository_and_legacy_run_denied(delivery):
    r=run(delivery)
    with pytest.raises(delivery.ContractError):delivery.delivery_record(r,delivery.failed_report(r),None,"2099-01-01T00:00:00Z")
    del r["spec"]["publication"]
    with pytest.raises(delivery.ContractError):delivery.publication_target(r)
