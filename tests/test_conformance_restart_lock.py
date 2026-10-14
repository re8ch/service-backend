import importlib.util
from pathlib import Path

import pytest

BASE=Path(__file__).parents[1]/"operations/api-conformance"


def controller(monkeypatch):
    monkeypatch.syspath_prepend(str(BASE))
    spec=importlib.util.spec_from_file_location("restart_controller",BASE/"controller.py")
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("phase",[None,"Starting","Running"])
def test_restart_retains_lock_on_uncertain_scheduled_run(monkeypatch,phase):
    c=controller(monkeypatch)
    r={"metadata":{"name":"run"},"spec":{"targetRef":"target","mode":"mutation","trigger":"schedule"},"status":{}}
    if phase:r["status"]["phase"]=phase
    target={"metadata":{"name":"target"},"spec":{"suspend":False}}
    monkeypatch.setattr(c,"list_resource",lambda plural:[r] if plural=="apiconformanceruns" else [target])
    monkeypatch.setattr(c,"collect_runs",lambda targets:None)
    monkeypatch.setattr(c,"discover",lambda targets:None)
    monkeypatch.setattr(c,"create_run",lambda *a,**k:pytest.fail("duplicate run submitted"))
    c.reconcile()
    assert c.active_runs()==[r]


def test_starting_is_persisted_before_job_submission(monkeypatch):
    c=controller(monkeypatch)
    target={"metadata":{"name":"target"},"spec":{"repository":"org/repo","commit":"a"*40,"branch":"main","imageDigest":"sha256:"+"b"*64,"schema":{"sha256":"c"*64},"github":{"installationId":"1"}}}
    monkeypatch.setattr(c,"validate_target",lambda value:value)
    calls=[]
    monkeypatch.setattr(c,"patch_status",lambda plural,name,status:calls.append(status["phase"]))
    def request(method,path,body):
        if path.endswith("/jobs"):
            assert calls==["Starting"]
            raise TimeoutError("submission response lost")
        return {}
    monkeypatch.setattr(c,"kube_request",request)
    with pytest.raises(TimeoutError):c.create_run(target,"read","schedule")
    assert calls==["Starting"]
