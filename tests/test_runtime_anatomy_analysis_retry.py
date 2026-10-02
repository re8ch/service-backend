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
