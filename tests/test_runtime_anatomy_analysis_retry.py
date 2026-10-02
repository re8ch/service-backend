"""A transient API failure must not permanently fail a storage analysis."""

import importlib.util
import urllib.error
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "clusters/re8ch/charts/runtime-anatomy/files/controller.py"


def test_analysis_retries_api_disconnect(monkeypatch):
    spec = importlib.util.spec_from_file_location("runtime_anatomy_controller", SOURCE)
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    failures = []
    monkeypatch.setattr(controller, "resolve_analysis_target", lambda obj: (_ for _ in ()).throw(
        urllib.error.URLError(ConnectionResetError("connection reset"))))
    monkeypatch.setattr(controller, "fail", lambda *args: failures.append(args))

    analysis = {"metadata": {"namespace": "cloud-disk", "name": "probe"}, "spec": {"target": {}}}
    try:
        controller.reconcile_analysis(analysis)
    except urllib.error.URLError:
        pass
    else:
        raise AssertionError("transient API failure was swallowed")
    assert failures == []


def test_analysis_waits_for_observer_evidence_after_capture_deadline(monkeypatch):
    spec = importlib.util.spec_from_file_location("runtime_anatomy_controller", SOURCE)
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    updates = []
    monkeypatch.setattr(controller, "analysis_evidence", lambda obj: None)
    monkeypatch.setattr(controller, "patch_status", lambda *args: updates.append(args))
    monkeypatch.setattr(controller.time, "time", lambda: 105)
    analysis = {"metadata": {"namespace": "cloud-disk", "name": "probe"},
                "spec": {}, "status": {"startedAt": "set", "deadlineEpoch": 100}}
    controller.reconcile_analysis(analysis)
    assert updates == []

    monkeypatch.setattr(controller.time, "time", lambda: 191)
    controller.reconcile_analysis(analysis)
    assert updates[0][2]["phase"] == "Incomplete"
